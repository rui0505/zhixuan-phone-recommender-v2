import csv
import json
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from starlette.testclient import TestClient
from api.app import create_app
from api.feedback import FeedbackStore

TOKEN = 'offline-test-admin-key-000000000000000000'


def payload(**changes):
    return dict(submission_id=str(uuid4()), nickname='测试用户', rating=4,
                category='suggestion', page='catalog', content='希望手机库可以更容易比较不同配置。', **changes)


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root/'state'/'feedback.sqlite3'
        self.now = 1800000000
        self.store = FeedbackStore(self.path, admin_token=TOKEN, clock=lambda:self.now)
        self.client = TestClient(create_app(self.root/'missing-catalog.sqlite', feedback_store=self.store),base_url='https://testserver')
        self.addCleanup(self.client.close)
        self.auth = {'Authorization':'Bearer '+TOKEN}

    def post(self, body=None, **kwargs):
        headers={'Content-Type':'application/json',**kwargs.pop('headers',{})}
        return self.client.post('/api/v1/feedback', content=json.dumps(body or payload()),headers=headers, **kwargs)

    def admin(self, query=''):
        return self.client.get('/api/v1/admin/feedback'+query, headers=self.auth)

    def moderate(self, receipt, status):
        return self.client.patch('/api/v1/admin/feedback/'+receipt['id'], json={'status':status}, headers=self.auth)

    def test_submission_is_private_until_review_then_can_be_withdrawn(self):
        result=self.post(); self.assertEqual(result.status_code,201)
        receipt=result.json(); self.assertEqual(receipt['status'],'pending')
        public=self.client.get('/api/v1/feedback').json()
        self.assertEqual(public['total'],0); self.assertIsNone(public['average_rating'])
        private=self.admin().json(); self.assertEqual(private['counts']['pending'],1)
        self.assertEqual(private['items'][0]['content'],'希望手机库可以更容易比较不同配置。')
        self.assertEqual(self.moderate(receipt,'published').status_code,200)
        public=self.client.get('/api/v1/feedback').json()
        self.assertEqual(public['total'],1); self.assertEqual(public['average_rating'],4)
        self.assertEqual(set(public['items'][0]),{'id','nickname','rating','category','page','content','created_at','published_at'})
        self.assertEqual(self.moderate(receipt,'rejected').status_code,200)
        self.assertEqual(self.client.get('/api/v1/feedback').json()['total'],0)

    def test_retry_and_restart_preserve_receipt_and_data(self):
        body=payload(); first=self.post(body).json()
        repeated=self.post(body); self.assertEqual(repeated.status_code,200); self.assertEqual(repeated.json(),first)
        restarted=TestClient(create_app(self.root/'missing', feedback_store=FeedbackStore(self.path,admin_token=TOKEN)),base_url='https://testserver')
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.get('/api/v1/admin/feedback',headers=self.auth).json()['total'],1)
        body['content']='同一编号的其他正文不允许静默覆盖。'
        self.assertEqual(self.post(body).status_code,409)

    def test_parallel_retry_commits_once(self):
        body=payload()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.store.submit(body,'same-source'),range(8)))
        self.assertEqual(len({r['id'] for r in results}),1)
        self.assertEqual(self.admin().json()['total'],1)

    def test_admin_routes_require_key_and_never_return_private_content(self):
        receipt=self.post().json()
        for url in ['/api/v1/admin/feedback','/api/v1/admin/feedback/export']:
            for headers in ({},{'Authorization':'Bearer wrong'},{'Authorization':'Bearer 中文'}):
                if '中文' in headers.get('Authorization',''):
                    self.assertFalse(self.store.authorized(headers['Authorization'])); continue
                response=self.client.get(url,headers=headers)
                self.assertEqual(response.status_code,401)
                self.assertNotIn('比较不同配置',response.text)
        response=self.client.patch('/api/v1/admin/feedback/'+receipt['id'],json={'status':'published'})
        self.assertEqual(response.status_code,401)
        self.assertEqual(self.client.get('/api/v1/feedback?status=pending').status_code,422)

    def test_remote_plain_http_cannot_use_management_key(self):
        remote=TestClient(create_app(self.root/'missing',feedback_store=self.store),client=('203.0.113.8',1234))
        self.addCleanup(remote.close)
        self.assertEqual(remote.get('/api/v1/admin/feedback',headers=self.auth).status_code,403)

    def test_validation_rejects_unknown_fields_types_and_limits(self):
        mutations=[{'rating':True},{'rating':0},{'rating':6},{'rating':'5'},
                   {'content':'短'},{'content':'x'*2001},{'content':'abc\x00def'},
                   {'content':'abc\ud800def'}, {'nickname':'x'*21},{'category':'unknown'},{'page':'bad'},
                   {'submission_id':'../bad'},{'status':'published'},{'ip':'forged'}]
        for mutation in mutations:
            with self.subTest(mutation=list(mutation)):
                body=payload(); body.update(mutation); self.assertEqual(self.post(body).status_code,422)
        for body in ([],None,{'content':'正文不完整'}):
            import json
            self.assertEqual(self.client.post('/api/v1/feedback',content=json.dumps(body),headers={'Content-Type':'application/json'}).status_code,422)
        self.assertEqual(self.post(payload(),headers={'Content-Type':'text/plain'}).status_code,415)
        self.assertEqual(self.client.get('/api/v1/feedback?page=0').status_code,422)
        self.assertEqual(self.client.get('/api/v1/feedback?page=1&page=2').status_code,422)
        self.assertEqual(self.client.get('/api/v1/feedback?page_size=101').status_code,422)
        self.assertEqual(self.client.get('/api/v1/feedback?page='+'9'*4400).status_code,422)

    def test_anonymous_default_and_plain_text_remain_literal(self):
        body=payload(); body['nickname']='  '; body['content']='<script>alert("评论")</script>'
        receipt=self.post(body).json(); self.moderate(receipt,'published')
        item=self.client.get('/api/v1/feedback').json()['items'][0]
        self.assertEqual(item['nickname'],'匿名用户'); self.assertEqual(item['content'],body['content'])

    def test_hourly_rate_limit_survives_restart_and_ignores_untrusted_headers(self):
        for i in range(10):self.assertEqual(self.post(headers={'X-Forwarded-For':f'fake-{i}'}).status_code,201)
        self.assertEqual(self.post().status_code,429)
        restarted=FeedbackStore(self.path,admin_token=TOKEN,clock=lambda:self.now)
        from api.contracts import ApiError
        with self.assertRaises(ApiError) as error:restarted.submit(payload(),'testclient')
        self.assertEqual(error.exception.status,429)
        self.now+=3600; self.assertEqual(self.post().status_code,201)
        with closing(sqlite3.connect(self.path)) as con:
            dump=''.join(con.iterdump())
        self.assertNotIn('testclient',dump); self.assertNotIn('fake-',dump)

    def test_admin_pagination_filters_and_csv_formula_safety(self):
        receipts=[]
        for content in ('=HYPERLINK("bad")','  +SUM(1,2)','正常的多行\n反馈内容'):
            body=payload(); body['content']=content; receipts.append(self.post(body).json())
        self.moderate(receipts[0],'published')
        self.assertEqual(self.admin('?status=pending&page_size=1&page=2').json()['total'],2)
        exported=self.client.get('/api/v1/admin/feedback/export',headers=self.auth)
        self.assertEqual(exported.status_code,200)
        rows=list(csv.DictReader(StringIO(exported.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows),3)
        text={row['content'] for row in rows}
        self.assertIn("'=HYPERLINK(\"bad\")",text); self.assertIn("'+SUM(1,2)",text)
        self.assertIn('正常的多行\n反馈内容',text)
        self.assertNotIn(TOKEN,exported.text)

    def test_bad_moderation_and_missing_ids_do_not_change_visibility(self):
        receipt=self.post().json()
        self.assertEqual(self.moderate(receipt,'approved').status_code,422)
        response=self.client.patch('/api/v1/admin/feedback/'+receipt['id'],json={'status':'published','content':'偷偷改文'},headers=self.auth)
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.moderate({'id':str(uuid4())},'published').status_code,404)
        self.assertEqual(self.client.get('/api/v1/feedback').json()['total'],0)

    def test_storage_failure_is_recoverable_without_internal_path_leak(self):
        bad=self.root/'blocked'; bad.write_text('file')
        client=TestClient(create_app(self.root/'missing',feedback_store=FeedbackStore(bad/'private.sqlite',admin_token=TOKEN)))
        self.addCleanup(client.close)
        response=client.post('/api/v1/feedback',json=payload())
        self.assertEqual(response.status_code,503); self.assertNotIn(str(bad),response.text)

    def test_generated_admin_key_is_persistent_and_not_publicly_served(self):
        store=FeedbackStore(self.path); store.submit(payload(),'source')
        key_path=self.path.parent/'feedback-admin.key'
        key=key_path.read_text().strip(); self.assertGreaterEqual(len(key),32)
        self.assertTrue(FeedbackStore(self.path).authorized('Bearer '+key))
        self.assertNotIn(key,self.client.get('/api/v1/feedback').text)
        self.assertEqual(self.client.get('/local_state/feedback-admin.key').status_code,404)

    def test_backup_is_consistent_and_includes_pending_comments(self):
        self.post(); target=self.root/'backups'/'feedback.sqlite3'
        self.store.backup(target)
        restored=FeedbackStore(target,admin_token=TOKEN)
        self.assertEqual(restored.list_admin([])['counts']['pending'],1)
        self.assertEqual(restored.public([])['total'],0)


if __name__=='__main__':unittest.main()
