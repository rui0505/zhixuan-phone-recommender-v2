from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from stage_derived import build_derived


class SpecScopeTests(unittest.TestCase):
    def rows(self):
        return [dict(_model_id='M1', _variant_id='V1', _sku_id='S1',
                     source_record_id='fixture#R1', 产品名称='realme GT5',
                     运行内存='16GB', 存储='512GB', **{'重量': 211, '电池典型容量(mAh)': 5240,
                     '充电功率': '150W有线', '无线充电': '无', '无线充电最高功率(W)': 0}),
                dict(_model_id='M1', _variant_id='V1', _sku_id='S2',
                     source_record_id='fixture#R2', 产品名称='realme GT5',
                     运行内存='24GB', 存储='1TB', **{'重量': 211, '电池典型容量(mAh)': 4600,
                     '充电功率': '240W有线', '无线充电': '无', '无线充电最高功率(W)': 0})]

    def test_common_spec_retains_every_source_row(self):
        entities, _ = build_derived({'conventions': {'storage_tb_to_gb': 1024}}, {}, self.rows(), [], [], {}, {}, None)
        self.assertIn('spec_provenance', entities, 'merged evidence map is not implemented')
        common = next(s for s in entities['specifications'] if s['spec_key'] == 'weight_g')
        self.assertEqual(common['subject_type'], 'model')
        records = [p['source_record_id'] for p in entities['spec_provenance'] if p['spec_id'] == common['spec_id']]
        self.assertEqual(sorted(records), ['fixture#R1', 'fixture#R2'])

    def test_different_config_values_are_not_promoted(self):
        entities, _ = build_derived({'conventions': {'storage_tb_to_gb': 1024}}, {}, self.rows(), [], [], {}, {}, None)
        records = [s for s in entities['specifications'] if s['spec_key'] == 'battery_capacity_mah']
        self.assertEqual({s['subject_id']: s['value_num'] for s in records}, {'S1': 5240, 'S2': 4600})
        self.assertTrue(all(s['subject_type'] == 'sku' for s in records))


if __name__ == '__main__':
    unittest.main()
