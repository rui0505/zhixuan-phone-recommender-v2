from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from catalog.repository import CatalogRepository
from catalog_fixture import create_catalog
from engine import rank_decision_input


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'c.sqlite'
        self.con=create_catalog(self.db);self.repo=CatalogRepository(str(self.db))
    def tearDown(self): self.con.close();self.temp.cleanup()
    def bundle(self,weights=None):
        from catalog.evidence import build_evidence
        di=self.repo.build_decision_input(['price_cny','weight_g'],weights=weights)
        result=rank_decision_input(di)
        return build_evidence(di,result,{'budget':{'max':3000}})
    def test_missing_fact_is_not_rendered_as_zero(self):
        self.con.execute("delete from specifications where spec_id='weight_g1'");self.con.commit()
        bundle=self.bundle({'price_cny':1,'weight_g':0})
        from catalog.evidence import render_template
        text=render_template(bundle)
        self.assertIn('重量：缺失',text)
        fact=next(f for f in bundle['facts'].values() if f['sku_id']=='S1' and f['metric_key']=='weight_g')
        self.assertIsNone(fact['value'])
    def test_template_has_same_models_and_versions_as_ranking(self):
        bundle=self.bundle()
        from catalog.evidence import render_template
        text=render_template(bundle)
        self.assertEqual([m['sku_id'] for m in bundle['models']],['S1','S2'])
        for expected in ('测试机1','测试机2','fixture-v1','topsis-1.0.0'): self.assertIn(expected,text)
    def test_unknown_fact_id_rejected(self):
        from catalog.evidence import validate_fact_selection
        with self.assertRaises(ValueError):validate_fact_selection({'fact_ids':['invented']},self.bundle())
    def test_free_prose_is_not_verified_explanation(self):
        from providers.recommend import explain_evidence
        class Fake:
            def select(self,bundle):return {'fact_ids':list(bundle['facts']), 'text':'iPhone 99 只卖99元'}
        text,status=explain_evidence(self.bundle(),selector=Fake())
        self.assertNotIn('iPhone 99',text);self.assertIn('模板',status)
    def test_duplicate_ids_and_non_string_ids_are_rejected(self):
        from catalog.evidence import validate_fact_selection
        bundle=self.bundle();key=next(iter(bundle['facts']))
        for payload in ({'fact_ids':[key,key]},{'fact_ids':[1]},{'text':'free prose'}):
            with self.subTest(payload=payload),self.assertRaises(ValueError):validate_fact_selection(payload,bundle)
    def test_bundle_tampering_is_rejected(self):
        from catalog.evidence import validate_fact_selection
        bundle=self.bundle();key=next(iter(bundle['facts']));bundle['facts'][key]['value']=99
        with self.assertRaises(ValueError):validate_fact_selection({'fact_ids':[key]},bundle)
    def test_result_from_other_dataset_is_rejected(self):
        from catalog.evidence import build_evidence
        di=self.repo.build_decision_input(['price_cny']);result=rank_decision_input(di);result.dataset_version='other'
        with self.assertRaises(ValueError):build_evidence(di,result,{})
    def test_bad_selector_error_does_not_expose_secret(self):
        from providers.recommend import explain_evidence
        class Fake:
            def select(self,bundle):raise RuntimeError('SECRET_TEST_DO_NOT_DISCLOSE')
        text,status=explain_evidence(self.bundle(),selector=Fake())
        self.assertNotIn('SECRET_TEST_DO_NOT_DISCLOSE',text+status)
    def test_sources_and_comparison_group_bind_to_the_same_fact(self):
        from catalog_fixture import insert
        insert(self.con,'sources',source_id='test-source',url='https://example.com/spec')
        self.con.execute("update specifications set source_id='test-source' where spec_id='weight_g1'");self.con.commit()
        bundle=self.bundle()
        fact=next(f for f in bundle['facts'].values() if f['sku_id']=='S1' and f['metric_key']=='weight_g')
        self.assertEqual(fact['comparison_group'],'spec::weight_g')
        self.assertEqual(fact['sources'][0]['url'],'https://example.com/spec')
