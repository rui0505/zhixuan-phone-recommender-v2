from pathlib import Path
import tempfile
import unittest
import pandas as pd
from catalog.repository import CatalogRepository
from catalog.adapter import build_input
from catalog_fixture import create_catalog
from pipeline import handle
from providers.jev import MockClassifier


class PipelineCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'c.sqlite'
        self.con=create_catalog(self.db);self.repo=CatalogRepository(str(self.db))
        self.df=pd.DataFrame({'brand':['品牌1','品牌2']})
    def tearDown(self): self.con.close();self.temp.cleanup()
    def turn(self,builder):
        return handle('预算1000到3000',self.df,classifier=MockClassifier(),write=False,decision_builder=builder)
    def test_rank_failure_turn_not_ok(self):
        def broken(needs,bridged): raise ValueError('simulated invalid matrix')
        turn=self.turn(broken)
        self.assertFalse(turn.ok);self.assertEqual(turn.stage,'failed');self.assertEqual(turn.models,[])
        self.assertTrue(turn.error)
    def test_incomparable_game_never_uses_refresh_as_proxy(self):
        turn=self.turn(lambda needs,bridged:build_input(self.repo,{}, {'game':1},None,'new'))
        self.assertEqual(turn.stage,'insufficient_comparable_data')
        self.assertFalse(turn.ok);self.assertEqual(turn.models,[])
        self.assertNotIn('放宽预算',turn.error)
    def test_success_uses_same_canonical_result_and_version(self):
        turn=self.turn(lambda needs,bridged:self.repo.build_decision_input(['price_cny']))
        self.assertTrue(turn.ok);self.assertFalse(turn.error)
        self.assertEqual([m['id'] for m in turn.models],['S1','S2'])
        self.assertEqual(turn.ranking.dataset_version,'fixture-v1')
        self.assertIn('sku_id',turn.to_dict()['ranking']['ranked'][0])
    def test_no_candidates_turn_is_not_success(self):
        turn=self.turn(lambda needs,bridged:self.repo.build_decision_input(['price_cny'],max_price=0))
        self.assertEqual(turn.stage,'no_candidates');self.assertFalse(turn.ok)
    def test_catalog_advice_binds_visible_models_and_versioned_facts(self):
        turn=handle('预算1000到3000',self.df,classifier=MockClassifier(),top_n=1,
            decision_builder=lambda needs,bridged:self.repo.build_decision_input(['price_cny']))
        self.assertEqual([m['sku_id'] for m in turn.evidence['models']],['S1'])
        self.assertIn('fixture-v1',turn.advice)
        self.assertIn('F-',turn.advice)
        self.assertNotIn('测试机2',turn.advice)
        self.assertFalse(turn.advice_from_llm)
        self.assertEqual(turn.to_dict()['evidence']['models'][0]['price_id'],'P1')
