import tempfile
import unittest
from pathlib import Path
from catalog.repository import CatalogRepository
from catalog_fixture import create_catalog, insert, game_run


class InputTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/'c.sqlite'
        self.con=create_catalog(self.db); self.repo=CatalogRepository(str(self.db))
    def tearDown(self):
        self.con.close(); self.tmp.cleanup()

    def test_virtual_price_storage_bind_selected_sources(self):
        di=self.repo.build_decision_input(['price_cny','storage_gb'])
        self.assertEqual(di['X'], [[1000,256],[2000,256]])
        self.assertEqual(di['provenance'][0]['criteria'][0]['price_id'], 'P1')
        self.assertEqual(di['provenance'][0]['criteria'][1]['source_record_id'], 'CONFIG1')

    def test_min_and_max_budget_apply_before_group_choice(self):
        di=self.repo.build_decision_input(['weight_g'], min_price=1500, max_price=2500)
        self.assertEqual([r['sku_id'] for r in self.repo.as_records(di['candidates'])], ['S2'])
        self.assertEqual(di['decision_status'], 'insufficient_comparable_data')

    def test_excluded_brands_before_group_choice(self):
        di=self.repo.build_decision_input(['weight_g'], excluded_brands=['品牌1'])
        self.assertEqual(di['coverage_summary']['candidate_count'],1)

    def test_no_candidates_has_priority_over_target_transform(self):
        di=self.repo.build_decision_input(['screen_size_inch'], max_price=0)
        self.assertEqual(di['decision_status'], 'no_candidates')

    def test_zero_weight_missing_does_not_disqualify(self):
        di=self.repo.build_decision_input(['weight_g','dxomark_photo'], weights={'weight_g':1})
        self.assertEqual(di['decision_status'],'ready'); self.assertEqual(di['weights']['dxomark_photo'],0)

    def test_invalid_and_extreme_weights(self):
        for value in (-1,float('nan'),float('inf'),True,0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.repo.build_decision_input(['weight_g'], weights={'weight_g':value})
        di=self.repo.build_decision_input(['weight_g','battery_capacity_mah'], weights={'weight_g':1e308,'battery_capacity_mah':1e308})
        self.assertEqual(di['weights'],{'weight_g':0.5,'battery_capacity_mah':0.5})

    def test_duplicate_unknown_and_double_count_camera_metrics(self):
        for keys in (['weight_g','weight_g'],['made_up'],['dxomark_overall','dxomark_photo']):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                self.repo.build_decision_input(keys)

    def test_unapproved_mapping_does_not_bypass_comparability(self):
        with self.assertRaises(ValueError):
            self.repo.build_decision_input(['weight_g'], comparison_group_overrides={'weight_g':'arbitrary'})

    def test_same_model_cannot_mix_different_game_runs(self):
        game_run(self.con,'M1','A1',{'honor_of_kings_avg_fps':119})
        game_run(self.con,'M1','A2',{'honor_of_kings_power_w':4})
        game_run(self.con,'M2','B',{'honor_of_kings_avg_fps':118,'honor_of_kings_power_w':5})
        di=self.repo.build_decision_input(['honor_of_kings_avg_fps','honor_of_kings_power_w'])
        self.assertEqual(di['eligible_indices'],[1])
        self.assertEqual(di['decision_status'],'insufficient_comparable_data')
        self.assertEqual(di['selected_run_ids'][1], {'honor_of_kings_avg_fps':'B','honor_of_kings_power_w':'B'})

    def test_different_hardware_blocks_shared_game_score(self):
        insert(self.con,'skus',sku_id='S3',model_id='M1',variant_id='V1',ram_raw='16GB',storage_raw='512GB')
        for sid, chip in [('S1','Chip A'),('S3','Chip B')]:
            insert(self.con,'specifications',spec_id='chip'+sid,subject_type='sku',subject_id=sid,spec_key='chipset',raw_value=chip)
        game_run(self.con,'M1','A',{'honor_of_kings_avg_fps':119})
        game_run(self.con,'M2','B',{'honor_of_kings_avg_fps':118})
        di=self.repo.build_decision_input(['honor_of_kings_avg_fps'])
        self.assertEqual(di['eligible_indices'],[1])
