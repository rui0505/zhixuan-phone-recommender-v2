import tempfile
from pathlib import Path
import unittest
from catalog.repository import CatalogRepository
from catalog_fixture import create_catalog
import engine


class DecisionRankingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.db=Path(self.temp.name)/'c.sqlite'
        self.con=create_catalog(self.db); self.repo=CatalogRepository(str(self.db))
    def tearDown(self): self.con.close(); self.temp.cleanup()
    def rank(self,di,**kwargs):
        self.assertTrue(hasattr(engine,'rank_decision_input'),'shared ranking entry missing')
        return engine.rank_decision_input(di,**kwargs)
    def test_price_only_orders_cheaper_candidate_first(self):
        r=self.rank(self.repo.build_decision_input(['price_cny']))
        self.assertEqual(r.ranked['sku_id'].tolist(),['S1','S2'])
        self.assertEqual(r.ranked['score'].tolist(),[1,0])
        self.assertEqual(r.dataset_version,'fixture-v1')
    def test_target_uses_absolute_deviation(self):
        self.con.execute("update specifications set value_num=6.1 where spec_id='screen_size_inch1'"); self.con.commit()
        r=self.rank(self.repo.build_decision_input(['screen_size_inch']),target_screen=6.6)
        self.assertEqual(r.ranked.iloc[0]['sku_id'],'S2')
    def test_nan_inf_weights_fail(self):
        for bad in (float('nan'),float('inf'),True,-1,0):
            di=self.repo.build_decision_input(['price_cny']);di['weights']['price_cny']=bad
            with self.subTest(bad=bad),self.assertRaises(ValueError): self.rank(di)
    def test_zero_weight_missing_does_not_block(self):
        di=self.repo.build_decision_input(['price_cny','dxomark_photo'],weights={'price_cny':1})
        self.assertEqual(len(self.rank(di).ranked),2)
    def test_one_model_and_all_ties_do_not_fake_unique_winner(self):
        singleton=self.rank(self.repo.build_decision_input(['price_cny'],max_price=1500))
        self.assertTrue(singleton.ranked.empty)
        self.assertEqual(singleton.status,'insufficient_comparable_data')
        tied=self.rank(self.repo.build_decision_input(['storage_gb']))
        self.assertEqual(tied.ranked['score'].tolist(),[.5,.5])
        self.assertIn('并列',tied.reason)
    def test_bad_matrix_dimensions_and_nan_active_values_are_rejected(self):
        for matrix in ([[1000]],[[float('nan')],[2000]],[[None],[2000]]):
            di=self.repo.build_decision_input(['price_cny']);di['X']=matrix
            with self.subTest(matrix=matrix),self.assertRaises(ValueError): self.rank(di)
    def test_huge_finite_values_normalize_without_overflow(self):
        di=self.repo.build_decision_input(['price_cny']);di['X']=[[1e308],[5e307]]
        ranked=self.rank(di).ranked
        self.assertEqual(ranked['sku_id'].tolist(),['S2','S1'])
        self.assertEqual(ranked['score'].tolist(),[1,0])
    def test_result_keeps_original_provenance_index_after_sort(self):
        di=self.repo.build_decision_input(['price_cny']);di['X']=[[2000],[1000]]
        r=self.rank(di)
        self.assertEqual(r.ranked['input_index'].tolist(),[1,0])
    def test_adapter_keeps_canonical_ids_and_maps_legacy_controls(self):
        from catalog import adapter
        di=adapter.build_input(self.repo,{'budget':{'min':0,'max':3000},'min_storage':256},
                               {'price':1,'weight':0},6.5,'new')
        self.assertEqual(di['weights'],{'price_cny':1,'weight_g':0})
        self.assertEqual(di['candidates']['sku_id'].tolist(),['S1','S2'])
    def test_game_control_uses_measured_fps_not_display_refresh(self):
        from catalog import adapter
        di=adapter.build_input(self.repo,{}, {'game':1},None,'new')
        self.assertEqual(di['criterion_definitions'][0]['metric_key'],'honor_of_kings_avg_fps')
        self.assertEqual(di['decision_status'],'insufficient_comparable_data')
