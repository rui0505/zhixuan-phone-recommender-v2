from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from api.app import create_app
from api.feedback import FeedbackStore
from catalog.release import ReleaseStore, file_sha
from tests.api_fixture import make_release, request_payload

TOKEN='offline-intake-admin-key-000000000000000000'

def phone():
    return {'model':{'brand':'新品牌','display_name':'测试新机 Pro','release_month':'2026-10','source_url':'https://example.org/spec'},
        'variant':{'variant_name':'标准版','region':'中国大陆','satellite_support':False,'is_default':True},
        'skus':[{'ram_gb':12,'storage_gb':256,'memory_type':None,'price':{'amount':2999,'price_kind':'official_current_price','condition':'new','observed_at':'2026-10-28','source_url':'https://example.org/product','source_note':''}}],
        'specifications':[{'key':k,'value_num':v,'unit':u,'raw_value':r} for k,v,u,r in [('weight_g',190,'g','190g'),('battery_capacity_mah',6000,'mAh','6000mAh'),('screen_size_inch',6.5,'inch','6.5英寸'),('chipset',None,None,'A18')]],'source_note':'合成API测试资料，非真实机型。'}

class IntakePublishTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=make_release(self.root)
        self.state=self.root/'state';self.catalog_state=self.state/'catalog'
        self.feedback=FeedbackStore(self.state/'feedback.sqlite3',admin_token=TOKEN)
        self.app=create_app(self.db,feedback_store=self.feedback,catalog_state=self.catalog_state)
        self.client=TestClient(self.app,base_url='https://testserver');self.addCleanup(self.client.close)
        self.auth={'Authorization':'Bearer '+TOKEN};self.original=file_sha(self.db)

    def create(self,mode='submit',body=None):
        response=self.client.post('/api/v1/intake/'+mode,json=body or phone(),headers=self.auth)
        self.assertEqual(response.status_code,201,response.text);return response.json()['record']

    def publish(self,record,**changes):
        body={'expected_revision':record['revision'],'expected_release':self.client.get('/api/v1/meta').json()['release'],'sources_verified':True,**changes}
        return self.client.post('/api/v1/intake/'+record['record_id']+'/publish',json=body,headers=self.auth)

    def test_unauthenticated_and_remote_http_cannot_read_or_write_queue(self):
        self.assertEqual(self.client.get('/api/v1/intake/queue').status_code,401)
        self.assertEqual(self.client.post('/api/v1/intake/submit',json=phone()).status_code,401)
        other=TestClient(self.app,client=('203.0.113.1',123));self.addCleanup(other.close)
        self.assertEqual(other.get('/api/v1/intake/queue',headers=self.auth).status_code,403)
        self.assertFalse(self.catalog_state.exists())

    def test_edit_draft_submit_and_publish_updates_catalog_without_mutating_base(self):
        partial=phone();partial['skus'][0]['price']['amount']=None
        draft=self.create('draft',partial)
        response=self.client.put('/api/v1/intake/'+draft['record_id'],json={'payload':phone(),'status':'submitted','expected_revision':draft['revision']},headers=self.auth)
        self.assertEqual(response.status_code,200,response.text);record=response.json()['record']
        self.assertGreater(record['revision'],draft['revision'])
        old=self.client.get('/api/v1/meta').json()['release']
        response=self.publish(record);self.assertEqual(response.status_code,200,response.text)
        current=response.json()['release'];self.assertNotEqual(current,old)
        self.assertEqual(self.client.get('/api/v1/meta').json()['counts']['phone_models'],3)
        listing=self.client.get('/api/v1/catalog?mode=all&q=测试新机').json();self.assertEqual(listing['total'],1)
        sku=listing['items'][0]['sku_id'];detail=self.client.get('/api/v1/skus/'+sku).json()
        self.assertEqual(detail['model']['display_name'],'测试新机 Pro')
        self.assertEqual(self.client.post('/api/v1/compare',json={'sku_ids':[sku,'S1']}).status_code,200)
        self.assertEqual(file_sha(self.db),self.original)
        stale=request_payload(ReleaseStore(self.db).get());self.assertEqual(self.client.post('/api/v1/recommend',json=stale).status_code,409)
        refreshed={**stale,'expected_release':current};ranked=self.client.post('/api/v1/recommend',json=refreshed)
        self.assertEqual(ranked.status_code,200,ranked.text)
        queue=self.client.get('/api/v1/intake/queue',headers=self.auth).json()
        self.assertEqual(queue['records'][0]['status'],'published')

    def test_stale_revision_or_missing_source_confirmation_cannot_publish(self):
        record=self.create();before=self.client.get('/api/v1/meta').json()['release']
        self.assertEqual(self.publish(record,sources_verified=False).status_code,422)
        self.assertEqual(self.publish(record,expected_revision=record['revision']+1).status_code,409)
        self.assertEqual(self.client.get('/api/v1/meta').json()['release'],before)

    def test_retry_and_concurrent_publish_do_not_duplicate_products(self):
        record=self.create();body={'expected_revision':record['revision'],'expected_release':self.client.get('/api/v1/meta').json()['release'],'sources_verified':True}
        url='/api/v1/intake/'+record['record_id']+'/publish'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.client.post(url,json=body,headers=self.auth),range(2)))
        self.assertTrue(all(result.status_code==200 for result in results),[r.text for r in results])
        self.assertEqual(self.client.get('/api/v1/catalog?mode=all&q=测试新机').json()['total'],1)
        self.assertEqual(self.client.get('/api/v1/meta').json()['counts']['phone_models'],3)

    def test_build_failure_keeps_previous_release_and_draft(self):
        record=self.create();before=self.client.get('/api/v1/meta').json()['release']
        with patch('catalog.publishing.build_snapshot',side_effect=OSError('private failure')):
            response=self.publish(record)
        self.assertEqual(response.status_code,503);self.assertNotIn('private failure',response.text)
        self.assertEqual(self.client.get('/api/v1/meta').json()['release'],before)
        self.assertEqual(self.client.get('/api/v1/intake/'+record['record_id'],headers=self.auth).json()['record']['status'],'submitted')

    def test_restart_and_feedback_moderation_are_independent(self):
        record=self.create();self.assertEqual(self.publish(record).status_code,200)
        other=TestClient(create_app(self.db,feedback_store=self.feedback,catalog_state=self.catalog_state),base_url='https://testserver');self.addCleanup(other.close)
        self.assertEqual(other.get('/api/v1/meta').json()['counts']['phone_models'],3)
        self.assertEqual(other.get('/api/v1/intake/queue',headers=self.auth).json()['total'],1)
        self.assertEqual(other.get('/api/v1/admin/feedback',headers=self.auth).status_code,200)
        self.assertEqual(other.get('/api/v1/feedback').json()['total'],0)

    def test_existing_rows_and_assets_survive_rebinding(self):
        # Real release sidecars are copied to the fixture but only fixture-specific review data is used.
        reviews={'format':'taobao-buyer-reviews-v1','snapshot_sha256':self.original,'dataset_version':'fixture','models':{}}
        (self.db.parent/'buyer_reviews.json').write_text(json.dumps(reviews),encoding='utf-8')
        record=self.create();self.assertEqual(self.publish(record).status_code,200)
        self.assertEqual(self.client.get('/api/v1/skus/S1/reviews').status_code,200)
        current=self.app.state.service.store.get()
        with closing(sqlite3.connect(current.repo.db_path)) as con,closing(sqlite3.connect(self.db)) as old:
            for table in ('phone_models','phone_variants','skus','price_snapshots','specifications','sources'):
                before=old.execute('SELECT * FROM '+table).fetchall();after=set(con.execute('SELECT * FROM '+table).fetchall())
                self.assertTrue(all(row in after for row in before),table)

    def test_existing_approved_images_remain_available_after_two_publications(self):
        from hashlib import sha256
        import shutil
        source=next((Path(__file__).resolve().parents[1]/'data/media').glob('*.webp'))
        media=self.db.parent/'media';media.mkdir();filename='fixture_M1.webp';shutil.copy2(source,media/filename)
        manifest={'approval':'OWNER_APPROVED_PUBLIC_DISPLAY','snapshot_sha256':self.original,
                  'models':{'M1':{'filename':filename,'sha256':sha256(source.read_bytes()).hexdigest(),'alt':'测试机型图'}}}
        (self.db.parent/'image_manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        old_image=self.client.get('/media/'+filename);self.assertEqual(old_image.status_code,200)
        record=self.create();self.assertEqual(self.publish(record).status_code,200)
        self.assertEqual(self.client.get('/media/'+filename).content,old_image.content)
        newer=phone();newer['model']['display_name']='第二台测试新机'
        another=self.create(body=newer);self.assertEqual(self.publish(another).status_code,200)
        self.assertEqual(self.client.get('/media/'+filename).content,old_image.content)
        self.assertEqual(self.client.get('/api/v1/meta').json()['features']['image_models'],1)

    def test_interrupted_acknowledgement_can_be_retried_without_duplicate_model(self):
        record=self.create();self.assertEqual(self.publish(record).status_code,200)
        # Simulate a crash after the atomic pointer update but before the intake transaction committed.
        with closing(sqlite3.connect(self.state/'intake.sqlite3')) as con,con:
            con.execute("UPDATE intake SET status='submitted',revision=?,release=NULL WHERE record_id=?",(record['revision'],record['record_id']))
        response=self.publish(record)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/v1/catalog?mode=all&q=测试新机').json()['total'],1)

    def test_duplicate_model_queue_validation_and_safe_exports(self):
        record=self.create();self.assertEqual(self.client.post('/api/v1/intake/submit',json=phone(),headers=self.auth).status_code,422)
        exported=self.client.get('/api/v1/intake/export',headers=self.auth)
        self.assertEqual(exported.status_code,200);self.assertEqual(exported.json()['records'][0]['record_id'],record['record_id'])
        self.assertNotIn(TOKEN,exported.text)
        self.assertEqual(self.client.get('/api/v1/intake/queue?page=0',headers=self.auth).status_code,422)
        self.assertEqual(self.client.get('/api/v1/intake/queue?page=1&page=2',headers=self.auth).status_code,422)

    def test_pointer_rejects_path_escape_or_mismatched_base(self):
        record=self.create();self.publish(record)
        pointer=self.catalog_state/'current.json';value=json.loads(pointer.read_text())
        value['directory']='../../escape';pointer.write_text(json.dumps(value))
        self.assertEqual(self.client.get('/api/v1/meta').status_code,503)

    def test_intake_backup_does_not_add_feedback_tables_or_change_source(self):
        self.create();source=self.state/'intake.sqlite3';digest=file_sha(source)
        backup=self.root/'intake-backup.sqlite3';FeedbackStore(source,admin_token=TOKEN).backup(backup)
        self.assertEqual(file_sha(source),digest)
        with closing(sqlite3.connect(backup)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM intake').fetchone()[0],1)
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='feedback'").fetchone())

    def test_published_text_camera_specs_participate_in_all_seven_reference_scores(self):
        for index in range(2):
            body=phone();body['model']['display_name']+=' '+str(index)
            body['specifications'].append({'key':'rear_camera','value_num':None,'unit':None,'raw_value':'5000万像素主摄 + 超广角 + 长焦'})
            record=self.create(body=body);self.assertEqual(self.publish(record).status_code,200)
        meta=self.client.get('/api/v1/meta').json()
        body={**meta['defaults'],'needs':{'budget':{'min':None,'max':None},'min_storage':None,'brands':{'include':['新品牌'],'exclude':[]}},'evaluation_mode':'spec_reference','confirmed':True,'expected_release':meta['release']}
        result=self.client.post('/api/v1/recommend',json=body)
        self.assertEqual(result.status_code,200,result.text);data=result.json()
        self.assertEqual(data['status'],'ready',data);self.assertEqual(data['ranked_total'],2)
        self.assertTrue(all(len(card['reference_scores'])==7 for card in data['ranked']))
        self.assertFalse(any(row['label']=='其他资料' for row in meta['source_summary']))

    def test_catalog_image_adapter_does_not_hash_database_for_every_card(self):
        from catalog.release import file_sha as actual_sha
        actual_db=Path(__file__).resolve().parents[1]/'data/catalog.sqlite'
        client=TestClient(create_app(actual_db,feedback_store=self.feedback,catalog_state=self.root/'large-catalog-state'))
        self.addCleanup(client.close);client.get('/api/v1/catalog?mode=all')
        with patch('catalog.release.file_sha',wraps=actual_sha) as hashed:
            response=client.get('/api/v1/catalog?mode=all&page_size=100')
        self.assertEqual(response.status_code,200)
        # Source-file verification belongs to request boundaries, not each rendered card.
        self.assertLess(hashed.call_count,30)

    def test_pointer_ack_failure_is_reconciled_before_edit_or_reject(self):
        from catalog import publishing
        real_replace=publishing.os.replace
        def fail_after_selection(source,destination):
            real_replace(source,destination)
            if Path(destination).name=='current.json':raise OSError('simulated acknowledgement loss')
        record=self.create()
        with patch('catalog.publishing.os.replace',side_effect=fail_after_selection):
            self.assertEqual(self.publish(record).status_code,503)
        self.assertEqual(self.client.get('/api/v1/catalog?mode=all&q=测试新机').json()['total'],1)
        changed=phone();changed['specifications'][0]['value_num']=210
        edited=self.client.put('/api/v1/intake/'+record['record_id'],json={'payload':changed,'status':'draft','expected_revision':record['revision']},headers=self.auth)
        self.assertEqual(edited.status_code,409)
        rejected=self.client.post('/api/v1/intake/'+record['record_id']+'/reject',json={'expected_revision':record['revision']},headers=self.auth)
        self.assertEqual(rejected.status_code,409)
        self.assertEqual(self.client.get('/api/v1/intake/'+record['record_id'],headers=self.auth).json()['record']['status'],'published')

if __name__=='__main__':unittest.main()
