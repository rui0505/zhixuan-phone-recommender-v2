"""Literal source cases catch configuration collapse and unsafe ranking inputs."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from repository import CatalogRepository
from stage_derived import build_derived


class DerivedScopeTests(unittest.TestCase):
    def setUp(self):
        # Values independently transcribed from the supplied price workbook.
        self.rows = [
            dict(_model_id='M1', _variant_id='V1', _sku_id='S1',
                 source_record_id='fixture#R1', 产品名称='realme GT5',
                 运行内存='16GB', 存储='512GB', **{'电池典型容量(mAh)': 5240,
                 '充电功率': '150W有线', '无线充电': '无', '无线充电最高功率(W)': 0}),
            dict(_model_id='M1', _variant_id='V1', _sku_id='S2',
                 source_record_id='fixture#R2', 产品名称='realme GT5',
                 运行内存='24GB', 存储='1TB', **{'电池典型容量(mAh)': 4600,
                 '充电功率': '240W有线', '无线充电': '无', '无线充电最高功率(W)': 0}),
        ]
        self.cfg = {'conventions': {'storage_tb_to_gb': 1024}}

    def specifications(self):
        entities, _ = build_derived(self.cfg, {}, self.rows, [], [], {}, {}, '2026-10-03')
        return entities['specifications']

    def test_storage_configuration_does_not_inherit_wrong_battery(self):
        specs = self.specifications()
        match = [s for s in specs if s['spec_key'] == 'battery_capacity_mah'
                 and s['subject_id'] in ('M1', 'S2')]
        self.assertEqual(match[-1]['value_num'], 4600)
        self.assertEqual(match[-1]['subject_type'], 'sku')

    def test_storage_configuration_does_not_inherit_wrong_charger(self):
        match = [s for s in self.specifications() if s['spec_key'] == 'wired_charge_w'
                 and s['subject_id'] in ('M1', 'S2')]
        self.assertEqual(match[-1]['value_num'], 240)

    def test_explicit_wireless_zero_is_preserved_not_invented(self):
        match = [s for s in self.specifications() if s['spec_key'] == 'wireless_charge_w']
        self.assertEqual(match[0]['value_num'], 0)
        self.assertEqual(match[0]['source_col'], '无线充电最高功率(W)')

    def test_approximate_charger_is_not_exact_numeric_input(self):
        self.rows[0]['充电功率'] = '约20W有线'
        match = [s for s in self.specifications() if s['spec_key'] == 'wired_charge_w'
                 and s['source_record_id'] == 'fixture#R1']
        self.assertIsNone(match[0]['value_num'])
        self.assertEqual(match[0]['parse_status'], 'approximate')


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = CatalogRepository(str(ROOT / 'catalog.sqlite'))

    def test_normalized_dxo_is_display_only(self):
        with self.assertRaises(ValueError):
            self.repo.build_decision_input(['dxomark_normalized_100'], mode='all')

    def test_group_selection_is_inside_new_candidates(self):
        di = self.repo.build_decision_input(['dxomark_photo'], mode='new')
        self.assertEqual(di['criterion_definitions'][0]['comparison_group_used'], 'dxomark_v6')
        self.assertEqual(sum(not row[0] for row in di['missing_mask']), 11)

    def test_invalid_mode_does_not_fall_back_to_all(self):
        with self.assertRaises(ValueError):
            self.repo.load_catalog(mode='nonsense')

    def test_negative_weight_rejected(self):
        with self.assertRaises(ValueError):
            self.repo.build_decision_input(['weight_g'], mode='all', weights={'weight_g': -1})

    def test_nan_inf_zero_and_boolean_weights_rejected(self):
        for value in (float('nan'), float('inf'), 0, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.repo.build_decision_input(['weight_g'], mode='all', weights={'weight_g': value})

    def test_unknown_metric_rejected(self):
        with self.assertRaises(ValueError):
            self.repo.build_decision_input(['imaginary_metric'], mode='all')

    def test_budget_and_storage_validate_domain(self):
        for kw in ({'max_price': -1}, {'max_price': float('nan')}, {'min_storage_gb': -1}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.repo.load_catalog(**kw)

    def test_missing_required_data_has_explicit_not_ready_status(self):
        di = self.repo.build_decision_input(mode='new')
        self.assertEqual(di.get('decision_status'), 'insufficient_comparable_data')
        self.assertEqual(di.get('eligible_indices'), [])
        self.assertEqual(len(di.get('backup_indices', [])), 51)

    def test_weights_normalize_without_inventing_missing_key_weights(self):
        di = self.repo.build_decision_input(['weight_g', 'battery_capacity_mah'], mode='all',
                                            weights={'weight_g': 2})
        self.assertEqual(di['weights'], {'weight_g': 1.0, 'battery_capacity_mah': 0.0})

    def test_realme_1tb_matrix_resolves_sku_specs(self):
        di = self.repo.build_decision_input(['battery_capacity_mah', 'wired_charge_w'], mode='all')
        cands = self.repo.as_records(di['candidates'])
        selected = [di['X'][i] for i, c in enumerate(cands)
                    if c['display_name'] == 'realme GT5' and c['ram_raw'] == '24GB']
        self.assertEqual(selected, [[4600, 240]])

    def test_honor_x70_wireless_configuration_values(self):
        di = self.repo.build_decision_input(['weight_g', 'has_wireless_charge', 'wireless_charge_w'], mode='all')
        cands = self.repo.as_records(di['candidates'])
        selected = [di['X'][i] for i, c in enumerate(cands)
                    if c['display_name'] == '荣耀X70' and c['storage_gb'] == 512]
        self.assertEqual(selected, [[199, 1, 80]])

    def test_details_include_sku_specific_chipset(self):
        det = self.repo.get_model_details(model_name='华为Mate 80 Pro')
        # Locate spelling from catalog; source display spelling is not rewritten.
        if det is None:
            name = next(m['display_name'] for m in self.repo.as_records(self.repo.list_models())
                        if 'Mate' in m['display_name'] and '80' in m['display_name'] and 'Pro' in m['display_name'])
            det = self.repo.get_model_details(model_name=name)
        specs = det['specifications']
        self.assertTrue(any(s['spec_key'] == 'chipset' and s['subject_type'] == 'sku'
                            and '9030 Pro' in str(s['raw_value']) for s in specs))


if __name__ == '__main__':
    unittest.main(verbosity=2)
