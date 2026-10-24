"""真实快照的契约测试：算法假设必须被数据满足。

夹具测试证明的是"夹具自洽"。只有直接读 `data/catalog.sqlite` 的用例，才能证明
"数据可用"——历史上游戏维度就是因为只测夹具、不测数据，静默失效了很久。

本地精简检出没有库时跳过；CI 与完整检出会跑。
"""
import unittest
from collections import defaultdict
from pathlib import Path

from catalog.comparability import game_group, select_game_runs
from catalog.repository import CatalogRepository, PRICE_PRIORITY_NEW, PRICE_PRIORITY_USED

DB = Path(__file__).resolve().parents[1] / 'data/catalog.sqlite'


class SnapshotContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DB.exists():
            raise unittest.SkipTest('data/catalog.sqlite 不在本次检出中')
        cls.repo = CatalogRepository(str(DB))
        cls.runs = cls.repo.as_records(cls.repo._q(
            "SELECT * FROM benchmark_runs WHERE benchmark='王者荣耀'"))
        cls.observations = cls.repo.as_records(cls.repo._q(
            "SELECT o.* FROM metric_observations o JOIN benchmark_runs r ON r.run_id=o.run_id "
            "WHERE r.benchmark='王者荣耀'"))

    def count(self, sql, params=()):
        return int(self.repo.as_records(self.repo._q(sql, params))[0]['n'])

    def test_game_runs_form_comparable_groups(self):
        groups = defaultdict(set)
        for run in self.runs:
            group, _ = game_group(run)
            if group:
                groups[group].add(run['model_id'])
        self.assertTrue(groups, '没有任何王者荣耀 run 通过条件校验，游戏维度会整体失效')
        self.assertGreaterEqual(max(len(v) for v in groups.values()), 2,
                                '没有一个可比组覆盖 2 个以上机型')

    def test_game_selection_yields_a_usable_group_with_disclosure(self):
        candidates = self.repo.as_records(self.repo.load_catalog(mode='all'))
        selection = select_game_runs(self.runs, self.observations,
                                     ['honor_of_kings_avg_fps'], candidates, {})
        group = selection['comparison_group']
        self.assertIsNotNone(group, f'选不出可比组；拒绝原因样例 {list(selection["rejection_reasons"].items())[:3]}')
        self.assertGreaterEqual(selection['coverage'][group], 2)
        detail = selection['comparison_group_detail']
        self.assertTrue(detail and detail.get('label'), '可比组缺少可读说明')
        # 未统一控制的条件必须如实交出去，不能因为不参与分组就假装一致
        self.assertTrue(detail.get('uncontrolled'), '未披露未控制条件')
        self.assertIn('ambient_temp_c', detail['uncontrolled'])

    def test_game_metric_has_real_values(self):
        models = self.count(
            "SELECT COUNT(DISTINCT r.model_id) n FROM metric_observations o "
            "JOIN benchmark_runs r ON r.run_id=o.run_id "
            "WHERE o.metric_key='honor_of_kings_avg_fps' AND o.value_num IS NOT NULL")
        self.assertGreaterEqual(models, 2, 'honor_of_kings_avg_fps 有值机型不足 2 个')

    def test_first_priority_price_kind_has_rows(self):
        """每个模式的首选价格档必须有数据，否则优先级表的排序对该模式是空话。"""
        for mode, priority in (('new', PRICE_PRIORITY_NEW), ('used', PRICE_PRIORITY_USED)):
            with self.subTest(mode=mode):
                self.assertGreater(
                    self.count('SELECT COUNT(*) n FROM price_snapshots WHERE price_kind=?',
                               (priority[0],)), 0,
                    f'{mode} 模式的首选档 {priority[0]} 在库里没有数据')

    def test_default_new_mode_has_enough_candidates(self):
        rows = self.repo.as_records(self.repo.load_catalog(mode='new'))
        models = {r['model_id'] for r in rows}
        self.assertGreaterEqual(len(models), 2, '默认新机模式的候选机型不足 2 个')

    def test_category_counts_do_not_depend_on_selected_category(self):
        """分类计数是"当前范围内各场景有多少机型"，切换分类不应改变它。

        这条守的是"先过滤后建卡"重构：曾经把计数挪到分类筛选之后，
        结果切到某个分类时其他分类的计数全变 0。
        """
        from api.contracts import parse_catalog_query
        from api.service import CatalogService
        from catalog.release import ReleaseStore

        service = CatalogService(ReleaseStore(DB))
        brands = service.meta()['brands']
        baseline = None
        for category in ('all', 'gaming', 'photography'):
            query = parse_catalog_query([('category', category)], brands)
            counts = service.catalog(query)['category_counts']
            if baseline is None:
                baseline = counts
                self.assertTrue(any(item['model_count'] > 0 for item in counts),
                                '快照里没有任何场景分类有内容，测试失去意义')
            else:
                self.assertEqual(counts, baseline, f'选中 {category} 后分类计数变了')


if __name__ == '__main__':
    unittest.main()
