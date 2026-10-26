"""芯片档位：名称归一化、查表、缺分不补 0、按 SKU 优先解析。

档位分来自第三方榜单换算（口径见 data/chipset-tiers.json），不是本站实测。
这些用例守住三件事：

1. 同一颗芯片的不同写法必须收敛（库里 `第三代骁龙8` 与 `骁龙8Gen3` 并存）
2. 查不到分返回 None，不能当 0 分——否则等于给出"性能很差"的假结论
3. 芯片记在 SKU 层时按 SKU 解析，不能张冠李戴到整个机型
"""
import json
import tempfile
import unittest
from pathlib import Path

from catalog.chipset_tiers import load_table, normalise_chipset, tier_for, table_path
from catalog_fixture import create_catalog, insert

TIERS = {
    'version': 'test-1.0.0',
    'basis': '测试用固定表，与真实档位表无关',
    'scores': {'骁龙8Gen3': 66, 'A18': 68, '麒麟9030Pro': 55},
}


class NormaliseTests(unittest.TestCase):
    def test_chinese_ordinal_and_digits_are_the_same_chip(self):
        for name in ('第三代骁龙8', '骁龙8Gen3', '骁龙8 Gen3', '骁龙8 gen 3'.replace('gen', 'Gen')):
            with self.subTest(name=name):
                self.assertEqual(normalise_chipset(name), '骁龙8Gen3')

    def test_s_variant_ordinal(self):
        self.assertEqual(normalise_chipset('第二代骁龙7S'), '骁龙7sGen2')

    def test_elite_with_generation_and_galaxy_suffix(self):
        self.assertEqual(normalise_chipset('第五代骁龙8至尊版forGalaxy'),
                         '骁龙8至尊版Gen5forGalaxy')
        self.assertEqual(normalise_chipset('骁龙8至尊版'), '骁龙8至尊版')

    def test_pro_suffix_spacing(self):
        self.assertEqual(normalise_chipset('麒麟9030 Pro'), '麒麟9030Pro')
        self.assertEqual(normalise_chipset('A19Pro'), 'A19Pro')

    def test_unknown_names_pass_through_unchanged(self):
        self.assertEqual(normalise_chipset('某个没见过的芯片'), '某个没见过的芯片')
        self.assertEqual(normalise_chipset(''), '')
        self.assertEqual(normalise_chipset(None), '')


class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, document, name='chipset-tiers.json'):
        path = self.dir / name
        path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        return path

    def test_missing_table_fails_loudly(self):
        with self.assertRaises(ValueError):
            load_table(self.dir / 'nope.json')

    def test_out_of_range_score_is_rejected(self):
        for bad in (0, 101, -5, True, '66'):
            with self.subTest(score=bad):
                path = self.write({'scores': {'X': bad}})
                with self.assertRaises(ValueError):
                    load_table(path)

    def test_unknown_chip_has_no_score_instead_of_zero(self):
        path = self.write(TIERS)
        scores, _ = load_table(path)
        self.assertEqual(tier_for('骁龙8Gen3', scores), 66)
        self.assertIsNone(tier_for('不存在的芯片', scores))

    def test_table_path_sits_next_to_the_database(self):
        self.assertEqual(table_path(self.dir / 'catalog.sqlite').name, 'chipset-tiers.json')


class MatrixIntegrationTests(unittest.TestCase):
    """档位分进入排名矩阵；缺分不补 0；按 SKU 优先解析。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / 'catalog.sqlite'
        self.con = create_catalog(self.db)
        self.addCleanup(self.con.close)
        # S1 的芯片只记在 SKU 层；S2 记在机型层。这正是华为 Mate 80 Pro 的形态。
        self.spec('sku', 'S1', '骁龙8Gen3')
        self.spec('model', 'M2', 'A18')
        (self.dir / 'chipset-tiers.json').write_text(
            json.dumps(TIERS, ensure_ascii=False), encoding='utf-8')
        from catalog.repository import CatalogRepository
        self.repo = CatalogRepository(str(self.db))

    def spec(self, subject_type, subject_id, chipset):
        insert(self.con, 'specifications', spec_id=f'chip-{subject_id}', subject_type=subject_type,
               subject_id=subject_id, spec_key='chipset', value_num=None, raw_value=chipset,
               parse_status='ok', comparison_group='spec::chipset', source_record_id='CONFIG1')
        self.con.commit()

    def tiers_by_sku(self, di):
        return {row['sku_id']: row['chipset_tier'] for row in self.repo.as_records(di['candidates'])}

    def test_sku_level_chipset_wins_over_model_level(self):
        di = self.repo.build_decision_input(['chipset_tier'], weights={'chipset_tier': 1})
        self.assertEqual(self.tiers_by_sku(di), {'S1': 66, 'S2': 68})

    def test_tier_fact_keeps_chip_spec_and_estimation_metadata(self):
        di = self.repo.build_decision_input(['chipset_tier'], weights={'chipset_tier': 1})
        fact = next(row for row in di['provenance'] if row['sku_id']=='S1')['criteria'][0]
        self.assertEqual(fact['spec_id'],'chip-S1')
        self.assertEqual(fact['raw_value'],'骁龙8Gen3')
        self.assertIn('estimated',fact['chipset_reference'])
        self.assertEqual(fact['chipset_reference']['version'],di['selection_rules']['chipset_table']['version'])

    def test_missing_chipset_stays_none_and_disqualifies_when_weighted(self):
        insert(self.con, 'phone_models', model_id='M3', display_name='测试机3',
               display_name_match_key='m3', brand='品牌3')
        insert(self.con, 'phone_variants', variant_id='V3', model_id='M3', variant_name='标准',
               name_is_source=0)
        insert(self.con, 'skus', sku_id='S3', variant_id='V3', model_id='M3', ram_raw='12GB',
               ram_gb=12, storage_raw='256GB', storage_gb=256, source_record_id='CONFIG3')
        insert(self.con, 'price_snapshots', price_id='P3', sku_id='S3', amount=3000,
               currency='CNY', price_kind='official_current_price', condition='new',
               observed_at='2026-01-01', source_record_id='PRICE3')
        self.con.commit()
        di = self.repo.build_decision_input(['chipset_tier'], weights={'chipset_tier': 1})
        self.assertIsNone(self.tiers_by_sku(di)['S3'])
        eligible = {self.repo.as_records(di['candidates'])[i]['sku_id'] for i in di['eligible_indices']}
        self.assertNotIn('S3', eligible)

    def test_table_is_not_required_when_the_metric_is_not_selected(self):
        """没选这个维度就不该依赖文件——否则一个没用的维度会拖垮整条链路。"""
        (self.dir / 'chipset-tiers.json').unlink()
        di = self.repo.build_decision_input(['weight_g'], weights={'weight_g': 1})
        self.assertEqual(di['decision_status'], 'ready')
        self.assertIsNone(di['selection_rules']['chipset_table'])

    def test_table_version_is_carried_into_selection_rules(self):
        di = self.repo.build_decision_input(['chipset_tier'], weights={'chipset_tier': 1})
        self.assertEqual(di['selection_rules']['chipset_table']['version'], 'test-1.0.0')
        criterion = next(c for c in di['criterion_definitions'] if c['metric_key'] == 'chipset_tier')
        self.assertEqual(criterion['criterion_layer'], 'virtual')
        self.assertEqual(criterion['direction'], 'benefit')
        self.assertEqual(criterion['name_cn'], '处理器档位')

    def test_missing_table_fails_when_the_metric_is_weighted(self):
        (self.dir / 'chipset-tiers.json').unlink()
        with self.assertRaises(ValueError):
            self.repo.build_decision_input(['chipset_tier'], weights={'chipset_tier': 1})


if __name__ == '__main__':
    unittest.main()
