"""Consumer reasons stay within the current, verified decision evidence."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest

from catalog.adapter import build_input
from catalog.evidence import build_evidence, digest
from catalog.release import ReleaseStore
from engine import rank_decision_input
from tests.api_fixture import make_release
from tests.catalog_fixture import game_run, insert


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sequence = 0

    def bundle(self, *, weights=None, budget=None, target_screen=6.5, prepare=None,
               mode='new', sku_ids=None):
        self.sequence += 1

        def records(con):
            insert(con, 'sources', source_id='spec-source', url='https://example.com/specs')
            insert(con, 'sources', source_id='price-source', url='https://example.com/prices')
            con.execute("UPDATE specifications SET source_id='spec-source'")
            con.execute("UPDATE price_snapshots SET source_id='price-source'")
            con.execute("UPDATE specifications SET value_num=6.4 WHERE spec_id='screen_size_inch1'")
            if prepare:
                prepare(con)

        db = make_release(Path(self.temp.name) / str(self.sequence), prepare=records)
        release = ReleaseStore(db).get()
        needs = {'budget': budget or {'min': None, 'max': None}, 'min_storage': None,
                 'brands': {'include': [], 'exclude': []}}
        selected_weights = weights or {'price_cny': 5, 'weight_g': 2, 'storage_gb': 2,
                                       'refresh_rate_hz': 1, 'screen_size_inch': 0,
                                       'dxomark_photo': 0, 'honor_of_kings_avg_fps': 0}
        di = build_input(release.repo, needs, selected_weights, target_screen, mode)
        result = rank_decision_input(di, target_screen=target_screen)
        self.assertEqual(result.status, 'ready')
        return build_evidence(di, result, needs, sku_ids=sku_ids)

    def reason(self, bundle, sku='S1'):
        self.assertIsNotNone(importlib.util.find_spec('api.presentation'),
                             'consumer explanation module is missing')
        from api.presentation import recommendation_reason
        return recommendation_reason(bundle, sku)

    def words(self, reason):
        return '\n'.join([reason['summary'], *reason['matches'], *reason['tradeoffs'],
                          *reason['limitations']])

    def resign(self, bundle):
        bundle['evidence_sha256'] = digest({k: v for k, v in bundle.items() if k != 'evidence_sha256'})
        return bundle

    def test_module_exists_for_real_version_bound_bundle(self):
        # An empty/default result or missing entry point cannot satisfy this API.
        value = self.reason(self.bundle())
        self.assertEqual(set(value), {'summary', 'matches', 'tradeoffs', 'limitations', 'fact_ids'})
        self.assertIsInstance(value['summary'], str)
        self.assertTrue(0 < len(value['matches']) <= 3)
        self.assertTrue(value['tradeoffs'] or value['limitations'])

    def test_budget_is_confirmed_and_price_keeps_date_and_condition(self):
        value = self.reason(self.bundle(weights={'price_cny': 1}, budget={'min': 900, 'max': 2100}))
        text = self.words(value)
        self.assertIn('1000', text)
        self.assertIn('2026-01-01', text)
        self.assertIn('新机', text)
        self.assertIn('预算', ' '.join(value['matches']))
        unlimited = self.words(self.reason(self.bundle(weights={'price_cny': 1})))
        self.assertNotIn('预算内', unlimited)
        self.assertNotIn('实时可买', text)

    def test_zero_weight_does_not_turn_an_unused_metric_into_a_match(self):
        value = self.reason(self.bundle(weights={'price_cny': 1, 'weight_g': 0,
                                                 'screen_size_inch': 0}))
        matches = ' '.join(value['matches'])
        self.assertIn('1000', matches)
        self.assertNotIn('191', matches)
        self.assertNotIn('6.4', matches)
        self.assertNotIn('更看重', self.words(value))

    def test_no_positive_supported_weight_does_not_force_a_match(self):
        bundle = self.bundle(weights={'price_cny': 1, 'weight_g': 0})
        # A valid signed projection with no effective weights must remain cautious.
        bundle['decision_context']['weights'] = {'price_cny': 0, 'weight_g': 0}
        value = self.reason(self.resign(bundle))
        self.assertEqual(value['matches'], [])
        self.assertTrue(value['limitations'])
        self.assertNotIn('更适合', value['summary'])

    def test_screen_match_and_tradeoff_follow_distance_to_target(self):
        bundle = self.bundle(weights={'screen_size_inch': 1}, target_screen=6.5)
        near = self.reason(bundle)
        far = self.reason(bundle, 'S2')
        self.assertIn('6.4', ' '.join(near['matches']))
        self.assertIn('6.5', ' '.join(near['matches']))
        self.assertIn('0.1', ' '.join(near['matches']))
        self.assertTrue(any('0.2' in row and '0.1' in row for row in far['tradeoffs']))
        self.assertNotIn('越大越好', self.words(near))
        self.assertNotIn('大屏更好', self.words(far))

    def test_spec_caveat_only_names_specs_present_in_this_decision(self):
        value = self.reason(self.bundle(weights={'screen_size_inch': 1}))
        self.assertIn('屏幕尺寸', ' '.join(value['limitations']))
        self.assertNotIn('重量', self.words(value))

    def test_missing_and_source_less_measurements_are_not_invented(self):
        def records(con):
            con.execute("DELETE FROM specifications WHERE spec_id='weight_g1'")
        value = self.reason(self.bundle(weights={'price_cny': 1, 'weight_g': 0,
                                                 'dxomark_photo': 0}, prepare=records))
        text = self.words(value)
        self.assertNotIn('重量 0', text)
        self.assertNotIn('拍照好', text)
        self.assertNotIn('游戏流畅', text)
        self.assertNotIn('真实续航', text)

        bundle = self.game_bundle(with_source=False)
        value = self.reason(bundle)
        self.assertEqual(value['matches'], [])
        self.assertTrue(any('来源' in row for row in value['limitations']))
        self.assertNotIn('118', ' '.join(value['matches']))

    def game_bundle(self, *, with_source=True):
        def records(con):
            insert(con, 'sources', source_id='game-source', url='https://example.com/game')
            for n, fps in ((1, 118), (2, 120)):
                game_run(con, f'M{n}', f'GAME{n}', {'honor_of_kings_avg_fps': fps})
            if with_source:
                con.execute("UPDATE benchmark_runs SET source_id='game-source'")
        return self.bundle(weights={'honor_of_kings_avg_fps': 1}, prepare=records)

    def test_game_text_stays_in_recorded_game_conditions_and_scope(self):
        value = self.reason(self.game_bundle())
        text = self.words(value)
        self.assertIn('王者荣耀', text)
        self.assertIn('118', ' '.join(value['matches']))
        self.assertIn('极高', text)
        self.assertIn('30', text)
        self.assertIn('机型', text)
        self.assertNotIn('游戏流畅', text)
        self.assertNotIn('游戏性能优秀', text)

    def photo_bundle(self):
        def records(con):
            insert(con, 'sources', source_id='photo-source', url='https://example.com/photo')
            for n, score in ((1, 130), (2, 140)):
                insert(con, 'benchmark_runs', run_id=f'PHOTO{n}', model_id=f'M{n}',
                       subject_type='model', subject_id=f'M{n}', benchmark='DXOMARK',
                       protocol='DXOMARK v5', comparison_group='dxomark_v5',
                       applicable_scope='model', source_id='photo-source')
                insert(con, 'metric_observations', observation_id=f'PHOTO{n}score',
                       run_id=f'PHOTO{n}', metric_key='dxomark_photo', value_num=score,
                       comparison_group='dxomark_v5')
        return self.bundle(weights={'dxomark_photo': 1}, prepare=records)

    def test_photo_uses_subscore_and_never_overall_photo_quality(self):
        value = self.reason(self.photo_bundle())
        text = self.words(value)
        self.assertIn('照片子分', text)
        self.assertIn('130', text)
        self.assertIn('第5版', text)
        self.assertNotIn('拍照好', text)
        self.assertNotIn('影像总分', text)

    def test_different_groups_or_protocols_never_produce_relative_claims(self):
        for change in ('comparison_group', 'protocol'):
            bundle = self.photo_bundle()
            other = next(f for f in bundle['facts'].values() if f['sku_id'] == 'S2')
            other[change] = 'dxomark_v4' if change == 'comparison_group' else 'DXOMARK v4'
            value = self.reason(self.resign(bundle))
            self.assertEqual(value['tradeoffs'], [])
            self.assertNotIn('140', self.words(value))
        single = self.reason(self.bundle(weights={'price_cny': 1}, sku_ids=['S1']))
        self.assertEqual(single['tradeoffs'], [])

    def test_used_estimated_snapshot_is_not_treated_as_a_new_price(self):
        def records(con):
            con.execute("UPDATE price_snapshots SET condition='used', is_estimated=1, "
                        "price_kind='used_estimate'")
        value = self.reason(self.bundle(weights={'price_cny': 1}, mode='used', prepare=records))
        text = self.words(value)
        self.assertIn('二手', text)
        self.assertIn('估计', text)
        self.assertIn('2026-01-01', text)
        self.assertNotIn('新机', text)

    def test_unknown_price_dates_or_conditions_do_not_count_as_matching_metadata(self):
        for field in ('observed_at', 'condition'):
            bundle = self.bundle(weights={'price_cny': 1})
            for model in bundle['models']:
                model[field] = None
            value = self.reason(self.resign(bundle), 'S2')
            self.assertEqual(value['tradeoffs'], [])
            self.assertNotIn('同日期、同成色', self.words(value))

    def test_question_mark_date_is_unknown_and_cannot_support_same_day_price_comparison(self):
        def records(con):
            con.execute("UPDATE price_snapshots SET observed_at='2026-10-02?'")
        value = self.reason(self.bundle(weights={'price_cny': 1}, prepare=records), 'S2')
        self.assertEqual(value['tradeoffs'], [])
        self.assertNotIn('2026-10-02', self.words(value))
        self.assertIn('日期不明确', self.words(value))

    def test_date_range_is_unknown_and_cannot_support_same_day_price_comparison(self):
        def records(con):
            con.execute("UPDATE price_snapshots SET observed_at='2026-10-02 至 2026-10-12'")
        value = self.reason(self.bundle(weights={'price_cny': 1}, prepare=records), 'S2')
        self.assertEqual(value['tradeoffs'], [])
        self.assertNotIn('2026-10-02', self.words(value))
        self.assertIn('日期不明确', self.words(value))

    def test_only_complete_calendar_dates_or_explicit_valid_datetimes_are_precise(self):
        from api.presentation import _record_date
        for value in ('2026-10-02', '2026-10-02T12:30:00', '2026-10-02 12:30',
                      '2026-10-02T12:30:00+08:00', '2026-10-02T04:30:00.123Z'):
            with self.subTest(value=value):
                self.assertEqual(_record_date(value), '2026-10-02')
        for value in ('约2026-10-02', '2026-10-02约', '2026-10-02T',
                      '2026-10-02T25:00:00', '2026-10-02T12:30:00+08:99',
                      '2026-02-30', '2026-10-02?12:30:00', '2026-10-02 12:30:00 extra'):
            with self.subTest(value=value):
                self.assertIsNone(_record_date(value))

    def test_unknown_sku_and_tampering_are_rejected_before_explanation(self):
        bundle = self.bundle()
        with self.assertRaises(ValueError):
            self.reason(bundle, 'ABSENT')
        first = next(iter(bundle['facts'].values()))
        first['value'] = 999
        with self.assertRaises(ValueError):
            self.reason(bundle)
        # Integrity must be checked before even attempting to select the SKU.
        with self.assertRaisesRegex(ValueError, '证据内容已变化'):
            self.reason(bundle, 'ABSENT')

    def test_pure_output_tracks_every_used_fact_without_exposing_ids(self):
        bundle = self.bundle(weights={'price_cny': 1, 'weight_g': 1, 'screen_size_inch': 1})
        before = deepcopy(bundle)
        value = self.reason(bundle, 'S2')
        self.assertEqual(bundle, before)
        self.assertEqual(value, self.reason(bundle, 'S2'))
        self.assertTrue(value['fact_ids'])
        self.assertEqual(len(value['fact_ids']), len(set(value['fact_ids'])))
        self.assertTrue(set(value['fact_ids']) <= set(bundle['facts']))
        # Price and weight are less favorable for S2; both sides support comparison.
        used = [bundle['facts'][ident] for ident in value['fact_ids']]
        self.assertTrue(any(f['sku_id'] == 'S1' for f in used))
        for field in ('summary', 'matches', 'tradeoffs', 'limitations'):
            text = value[field] if isinstance(value[field], str) else '\n'.join(value[field])
            for forbidden in ('F-', 'sha256', 'comparison_group', 'source_record_id',
                              'decision_context', '购买概率', '满意度'):
                self.assertNotIn(forbidden, text)
            self.assertNotIn('{', text)


if __name__ == '__main__':
    unittest.main()
