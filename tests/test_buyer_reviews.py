import csv
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from starlette.testclient import TestClient
from tests.api_fixture import make_release
from api.app import create_app
from scripts.import_buyer_reviews import build


class BuyerReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=make_release(self.root)
        with closing(sqlite3.connect(self.db)) as con:
            self.mid,self.brand,self.name=con.execute('SELECT model_id,brand,display_name FROM phone_models ORDER BY model_id').fetchone()
        self.source=self.root/'reviews.csv'
        self.snapshot=self.db.parent/'buyer_reviews.json'
        self.original=self.db.read_bytes()

    def row(self,**changes):
        return {'品牌':self.brand,'型号':self.name,'商品ID':'1234567','商品标题':'商品标题',
                '用户名':'买家私密名称','评价等级':'好评','评价时间':'2026-10-25 12:30:00',
                '评价内容':'日常使用的体验。','购买规格':'12GB+256GB','追评内容':'使用一周的追评。','采集时间':'2026-10-27',**changes}

    def publish(self,rows):
        with self.source.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=self.row().keys());writer.writeheader();writer.writerows(rows)
        data=build(self.source,self.db)
        self.snapshot.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        self.client=TestClient(create_app(self.db));self.addCleanup(self.client.close)
        return data

    def test_import_keeps_source_nickname_text_and_followup_and_removes_contacts(self):
        content='  原文 <script>alert(1)</script> 手机13812345678 email hello@example.com  '
        data=self.publish([self.row(评价内容=content)])
        record=data['models'][self.mid][0]
        self.assertTrue(record['content'].startswith('  原文'))
        self.assertIn('<script>',record['content']);self.assertIn('追评',record['follow_up'])
        self.assertEqual(record['username'],'买家私密名称')
        encoded=json.dumps(data,ensure_ascii=False)
        for private in ('13812345678','hello@example.com'):self.assertNotIn(private,encoded)
        self.assertEqual(self.db.read_bytes(),self.original)

    def test_match_requires_exact_brand_and_model_and_deduplicates_source_records(self):
        row=self.row()
        data=self.publish([row,row,self.row(型号=''),self.row(型号=self.name+' Pro'),self.row(品牌='Other')])
        self.assertEqual(data['imported_rows'],1)
        self.assertEqual(data['excluded'],{'duplicate':1,'missing_model':1,'unmatched_model':2})

    def test_api_filters_default_good_separately_and_paginates_latest_first(self):
        self.publish([self.row(评价内容=str(i),评价等级=grade,评价时间=f'2026-10-{20+i:02}')
                      for i,grade in enumerate(['差评','好评','中评','默认好评','好评'])])
        response=self.client.get('/api/v1/skus/S1/reviews?page_size=2').json()
        self.assertEqual(response['sample_total'],5);self.assertEqual(response['grade_counts']['good'],2)
        self.assertEqual(response['grade_counts']['default_good'],1)
        self.assertEqual([x['content'] for x in response['items']],['4','3'])
        bad=self.client.get('/api/v1/skus/S1/reviews?grade=bad').json()
        self.assertEqual(bad['total'],1);self.assertEqual(bad['items'][0]['content'],'0')
        self.assertEqual(bad['scope'],'model');self.assertEqual(bad['model']['model_id'],self.mid)
        self.assertEqual(bad['items'][0]['username'],'买家私密名称')

    def test_uncollected_model_and_missing_snapshot_are_honest_empty_states(self):
        self.publish([self.row()])
        empty=self.client.get('/api/v1/skus/S2/reviews').json()
        self.assertEqual(empty['sample_total'],0);self.assertTrue(empty['available'])
        self.snapshot.unlink()
        empty=self.client.get('/api/v1/skus/S1/reviews').json()
        self.assertFalse(empty['available']);self.assertEqual(empty['items'],[])

    def test_invalid_queries_and_unknown_sku_are_rejected(self):
        self.publish([self.row()])
        for query in ('grade=bad&grade=good','grade=5','page=0','page_size=101','file=secret','q=text'):
            self.assertEqual(self.client.get('/api/v1/skus/S1/reviews?'+query).status_code,422,query)
        self.assertEqual(self.client.get('/api/v1/skus/NOPE/reviews').status_code,404)

    def test_corrupt_or_mismatched_dataset_fails_without_exposing_paths(self):
        data=self.publish([self.row()]);data['snapshot_sha256']='wrong'
        p=self.snapshot;p.write_text(json.dumps(data),encoding='utf-8')
        response=self.client.get('/api/v1/skus/S1/reviews')
        self.assertEqual(response.status_code,503);self.assertNotIn(str(self.root),response.text)
        p.write_text('broken',encoding='utf-8')
        self.assertEqual(self.client.get('/api/v1/skus/S1/reviews').status_code,503)

    def test_unapproved_extra_field_is_not_served(self):
        data=self.publish([self.row()]);data['models'][self.mid][0]['phone_number']='private'
        self.snapshot.write_text(json.dumps(data),encoding='utf-8')
        r=self.client.get('/api/v1/skus/S1/reviews')
        self.assertEqual(r.status_code,503);self.assertNotIn('private',r.text)
