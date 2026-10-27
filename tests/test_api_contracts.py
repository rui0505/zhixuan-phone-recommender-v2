"""Breaks caught: silently broadened filters, bool/NaN scores, unbounded lists."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest

from catalog.release import ReleaseStore
from tests.api_fixture import make_release, request_payload


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('api'), 'API contracts are absent')
        from api import contracts
        self.c = contracts
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.release = ReleaseStore(make_release(Path(self.temp.name))).get()
        self.body = request_payload(self.release)
        self.brands = ['品牌1','品牌2','Apple','华为']

    def parse(self, body=None):
        return self.c.parse_recommendation(self.body if body is None else body, self.brands)

    def test_default_request_and_aliases(self):
        self.body['needs']['brands']['include']=['苹果']
        result=self.parse()
        self.assertEqual(result.needs['brands']['include'],['Apple'])
        self.assertEqual(self.body['needs']['brands']['include'],['苹果'])
        self.assertEqual(result.target_screen,6.5)
        self.assertEqual(result.mode,'new')

    def test_weight_numeric_domain(self):
        for value in (-1,11,True,'5',float('nan'),float('inf'),None):
            with self.subTest(value=value):
                body=deepcopy(self.body);body['weights']['price_cny']=value
                with self.assertRaises(self.c.ApiError):self.parse(body)
        for value in (0,10):
            body=deepcopy(self.body);body['weights']['price_cny']=value
            self.assertEqual(self.parse(body).weights['price_cny'],value)

    def test_zero_unknown_or_alias_weights_rejected(self):
        for weights in ({k:0 for k in self.body['weights']},{'made_up':1},{'price':1,'price_cny':1},{}):
            with self.subTest(weights=weights),self.assertRaises(self.c.ApiError):
                self.parse(request_payload(self.release,weights=weights))

    def test_required_confirmation_and_unknown_fields(self):
        for value in (False,1,None,'true'):
            with self.assertRaises(self.c.ApiError):self.parse(request_payload(self.release,confirmed=value))
        for key in self.body:
            body=deepcopy(self.body);del body[key]
            with self.subTest(missing=key),self.assertRaises(self.c.ApiError):self.parse(body)
        body=deepcopy(self.body);body['db_path']='untrusted'
        with self.assertRaises(self.c.ApiError):self.parse(body)
        body=deepcopy(self.body);body['needs']['budget']['guess']=123
        with self.assertRaises(self.c.ApiError):self.parse(body)

    def test_screen_domain_and_active_target(self):
        for target in (3,10):
            result=self.parse(request_payload(self.release,target_screen=target,weights={'screen_size_inch':1}))
            self.assertEqual(result.target_screen,target)
        for target in (2.9,10.1,None,True,'6.5',float('nan')):
            with self.subTest(target=target),self.assertRaises(self.c.ApiError):
                self.parse(request_payload(self.release,target_screen=target,weights={'screen_size_inch':1}))
        # 屏幕维度不启用时，目标尺寸可以为空（其余维度保持默认，否则全零会被拒）
        no_screen={**request_payload(self.release)['weights'],'screen_size_inch':0}
        self.assertIsNone(self.parse(request_payload(
            self.release,target_screen=None,weights=no_screen)).target_screen)

    def test_budget_storage_and_nested_shape(self):
        for value in (-1,True,'500',float('inf')):
            body=deepcopy(self.body);body['needs']['budget']['max']=value
            with self.assertRaises(self.c.ApiError):self.parse(body)
        body=deepcopy(self.body);body['needs']['budget']={'min':2000,'max':1000}
        with self.assertRaises(self.c.ApiError):self.parse(body)
        for value in (0,-1,True,'128',float('nan')):
            body=deepcopy(self.body);body['needs']['min_storage']=value
            with self.assertRaises(self.c.ApiError):self.parse(body)
        for needs in (None,[],{'budget':{},'min_storage':None,'brands':{}}):
            with self.assertRaises(self.c.ApiError):self.parse(request_payload(self.release,needs=needs))

    def test_brand_conflict_unknown_and_shape(self):
        for include,exclude in ((['苹果'],['Apple']),(['未知'],[]),('华为',[]),([True],[]),([''],[])):
            body=deepcopy(self.body);body['needs']['brands']={'include':include,'exclude':exclude}
            with self.assertRaises(self.c.ApiError):self.parse(body)
        with self.assertRaises(self.c.ApiError):self.parse(request_payload(self.release,mode='anything'))

    def test_release_key_shape(self):
        for key in ({},{**self.release.key,'snapshot_sha256':'short'},{**self.release.key,'secret':'x'}):
            with self.assertRaises(self.c.ApiError):self.parse(request_payload(self.release,expected_release=key))

    def test_pagination_exact_bounds(self):
        self.assertEqual(self.c.parse_pagination([]),self.c.Pagination(1,24))
        self.assertEqual(self.c.parse_pagination([('page_size','100')]).page_size,100)
        for pairs in ([('page_size','101')],[('page','0')],[('page','1'),('page','2')],[('page','')],[('page','1.1')]):
            with self.subTest(pairs=pairs),self.assertRaises(self.c.ApiError):self.c.parse_pagination(pairs)

    def test_query_values_not_silently_dropped(self):
        query=self.c.parse_catalog_query([('include','苹果'),('exclude','华为'),('q',"手机% '测试"),('min_storage','128')],self.brands)
        self.assertEqual(query.needs['brands'],{'include':['Apple'],'exclude':['华为']})
        self.assertEqual(query.q,"手机% '测试")
        self.assertEqual(query.needs['min_storage'],128)
        for pairs in ([('oops','1')],[('mode','new'),('mode','all')],[('min_price','')],[('max_price','NaN')],[('min_storage','0')]):
            with self.subTest(pairs=pairs),self.assertRaises(self.c.ApiError):self.c.parse_catalog_query(pairs,self.brands)

    def test_comparison_bounds_and_ids(self):
        self.assertEqual(self.c.parse_compare({'sku_ids':['S1']}),['S1'])
        self.assertEqual(len(self.c.parse_compare({'sku_ids':['S1','S2','S3','S4']})),4)
        for ids in ([],['S1','S1'],['S1','S2','S3','S4','S5'],[1],[''],['../data/catalog.sqlite']):
            with self.assertRaises(self.c.ApiError):self.c.parse_compare({'sku_ids':ids})

    def test_export_never_accepts_uploaded_scores(self):
        body={'request':self.body,'fingerprints':{'decision_sha256':'a'*64,'ranking_sha256':'b'*64}}
        request,fps=self.c.parse_export(body,self.brands)
        self.assertEqual(request.mode,'new');self.assertEqual(fps['ranking_sha256'],'b'*64)
        body['scores']=[100]
        with self.assertRaises(self.c.ApiError):self.c.parse_export(body,self.brands)
