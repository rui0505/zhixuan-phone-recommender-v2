from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stage_ids
import build_dataset


class PilotTests(unittest.TestCase):
    def test_pilot_filters_business_entities_without_reassigning_ids(self):
        fn = getattr(build_dataset, 'filter_pilot_entities', None)
        self.assertTrue(callable(fn), 'pilot entity filter is not implemented')
        entities = {'phone_models': [{'model_id': 'M1'}, {'model_id': 'M2'}],
                    'phone_variants': [{'variant_id': 'V1', 'model_id': 'M1'}, {'variant_id': 'V2', 'model_id': 'M2'}],
                    'skus': [{'sku_id': 'S1', 'variant_id': 'V1', 'model_id': 'M1'}, {'sku_id': 'S2', 'variant_id': 'V2', 'model_id': 'M2'}],
                    'price_snapshots': [{'price_id': 'P1', 'sku_id': 'S1'}, {'price_id': 'P2', 'sku_id': 'S2'}],
                    'specifications': [{'spec_id': 'SP1', 'subject_type': 'sku', 'subject_id': 'S1'}, {'spec_id': 'SP2', 'subject_type': 'model', 'subject_id': 'M2'}],
                    'benchmark_runs': [{'run_id': 'R1', 'model_id': 'M1'}, {'run_id': 'R2', 'model_id': 'M2'}],
                    'metric_observations': [{'run_id': 'R1'}, {'run_id': 'R2'}],
                    'import_mapping': [{'target_type': 'sku', 'target_id': 'S1'}, {'target_type': 'sku', 'target_id': 'S2'}]}
        assets = {'assets': [], 'covers': [], 'members': [], 'stats': {}}
        fn(entities, assets, {'S1'})
        self.assertEqual([s['sku_id'] for s in entities['skus']], ['S1'])
        self.assertEqual([s['spec_id'] for s in entities['specifications']], ['SP1'])
        self.assertEqual([s['run_id'] for s in entities['benchmark_runs']], ['R1'])
        self.assertEqual(len(entities['metric_observations']), 1)

    def test_pilot_has_20_unique_registered_skus(self):
        fn = getattr(stage_ids, 'select_pilot_ids', None)
        self.assertTrue(callable(fn), 'stable pilot selector is not implemented')
        names = ['realmeGT5', '荣耀X70', '华为Mate80Pro', 'OPPO Find X8 Pro', 'vivo X200 Pro']
        names += ['合成测试机型' + str(i) for i in range(20)]
        names += ['OPPO Find X8 Pro']
        rows = [{'_sku_id': f'S{i:04d}', '产品名称': name, '版本/备注': '卫星版' if i == 3 else '',
                 '运行内存': '24GB' if i == 0 else '16GB', '存储': '1TB' if i == 0 else '512GB'}
                for i, name in enumerate(names)]
        registry = [{'id': row['_sku_id'], 'id_type': 'sku'} for row in rows]
        selected = fn(rows, registry)
        self.assertEqual(len(selected), 20)
        self.assertEqual(len(set(selected)), 20)
        self.assertEqual(selected, fn(list(reversed(rows)), registry))
        self.assertTrue({'S0000', 'S0001', 'S0002', 'S0003'}.issubset(selected))
        self.assertIn('S0025', selected, 'satellite configuration must have its standard counterpart')


if __name__ == '__main__':
    unittest.main()
