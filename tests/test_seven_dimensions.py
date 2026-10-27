from pathlib import Path
import tempfile
import unittest

from api.needs import NeedsService
from api.contracts import ApiError, parse_recommendation
from providers.jev import MockClassifier
from schemas import CRITERIA
from tests.api_fixture import make_release, request_payload
from catalog.release import ReleaseStore


class SevenDimensionTests(unittest.TestCase):
    def draft(self, text):
        with tempfile.TemporaryDirectory() as root:
            return NeedsService(enabled=True,key='OFFLINE',classifier=MockClassifier(),
                quota_path=Path(root)/'quota.sqlite').extract(text,[])

    def test_capacity_does_not_score_storage_or_battery_duration(self):
        for text,storage,battery in [('存储容量越大越好',7,2),('电池容量越大越好',3,7),('电池耐用，续航要长',3,2)]:
            with self.subTest(text=text):
                value=self.draft(text)
                self.assertEqual(value['draft']['weights']['storage_gb'],storage)
                self.assertEqual(value['draft']['weights']['battery_capacity_mah'],battery)
                self.assertEqual(value['draft']['weights']['battery_mixed_usage_duration_min'],0)

    def test_day_long_battery_requests_stay_duration_needs(self):
        for text in ('电池能用一天最重要','电池一天一充就行','电池续航要好'):
            with self.subTest(text=text):
                value=self.draft(text)
                self.assertEqual(value['draft']['weights']['battery_capacity_mah'],2)
                self.assertIn('续航',[p['need'] for p in value['extraction']['unsupported_needs']])

    def test_hybrid_rejects_a_price_score_and_retains_budget_and_brand(self):
        with tempfile.TemporaryDirectory() as root:
            release=ReleaseStore(make_release(Path(root))).get()
            body=request_payload(release,method='hybrid_rrf')
            body['needs']['budget']={'min':2000,'max':5000}
            body['needs']['brands']={'include':['品牌1'],'exclude':['品牌2']}
            request=parse_recommendation(body,['品牌1','品牌2'])
            self.assertEqual(request.needs,body['needs'])
            self.assertNotIn('price_cny',request.weights)
            body['weights']['price_cny']=5
            with self.assertRaises(ApiError):parse_recommendation(body,['品牌1','品牌2'])

    def test_extraction_uses_seven_table_rows_without_price_or_refresh(self):
        value=self.draft('预算5000元以内，拍照最重要，处理器要强')
        self.assertEqual({p['criterion'] for p in value['extraction']['preferences']},set(CRITERIA))
        self.assertEqual(value['draft']['weights']['dxomark_photo'],9)
        self.assertEqual(value['draft']['weights']['chipset_tier'],7)
        self.assertNotIn('price_cny',value['draft']['weights'])
        self.assertEqual(value['draft']['needs']['budget']['max'],5000)

    def test_real_chipset_only_ranking_renders_reasons_without_a_server_error(self):
        from api.app import create_app
        from starlette.testclient import TestClient
        with TestClient(create_app()) as client:
            meta=client.get('/api/v1/meta').json()
            body={**meta['defaults'],'needs':{'budget':{'min':None,'max':None},'min_storage':None,
                'brands':{'include':[],'exclude':[]}},'weights':{'chipset_tier':9},
                'method':'hybrid_rrf','confirmed':True,'expected_release':meta['release'],'defaulted_metrics':[]}
            result=client.post('/api/v1/recommend',json=body)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['status'],'ready')
