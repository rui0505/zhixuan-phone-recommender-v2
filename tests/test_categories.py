"""Breaks caught: fabricated suitability, unsafe provenance, and cross-scope evidence."""
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from catalog.repository import CatalogRepository
from tests.catalog_fixture import create_catalog, insert


class CategoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('api.categories'),
                             'Read-only scenario categories are absent')
        self.categories = importlib.import_module('api.categories')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'catalog.sqlite'
        self.con = create_catalog(self.db)
        self.addCleanup(self.con.close)
        self.repo = CatalogRepository(str(self.db))

    def observation(self, run_id, metric_key='honor_of_kings_avg_fps', value=59.8,
                    *, url='https://example.test/review', model_id='M1',
                    subject_type='model', subject_id=None, applicable_scope='model',
                    variant_id=None, sku_id=None, protocol='review-v1', missing_reason=None):
        source_id = 'source-' + run_id
        if url is not None:
            insert(self.con, 'sources', source_id=source_id, url=url,
                   publisher='测试评测机构', raw_text=r'C:\private\source.xlsx')
        insert(self.con, 'benchmark_runs', run_id=run_id, subject_type=subject_type,
               subject_id=subject_id or model_id, model_id=model_id,
               variant_id=variant_id, sku_id=sku_id, applicable_scope=applicable_scope,
               source_id=source_id if url is not None else None, protocol=protocol,
               institution='测试评测机构', conditions_json='{"quality":"high"}',
               benchmark='受控测试', comparison_group='group-' + run_id)
        insert(self.con, 'metric_observations', observation_id='obs-' + run_id,
               run_id=run_id, metric_key=metric_key, value_num=value,
               unit='FPS' if metric_key == 'honor_of_kings_avg_fps' else '分',
               missing_reason=missing_reason, verification_status='machine_checked')
        self.con.commit()

    def category(self, category_id, model_id='M1'):
        return next(c for c in self.categories.model_categories(self.repo)[model_id]
                    if c['id'] == category_id)

    def test_all_models_have_three_explicit_pending_categories_when_evidence_is_absent(self):
        result = self.categories.model_categories(self.repo)
        self.assertEqual(set(result), {'M1', 'M2'})
        for items in result.values():
            self.assertEqual([(c['id'], c['label']) for c in items], [
                ('gaming', '游戏手机'), ('senior', '长辈友好'), ('photography', '拍照手机')])
            for item in items:
                self.assertEqual(set(item), {'id', 'label', 'status', 'reason',
                                             'evidence_refs', 'rule_version'})
                self.assertEqual(item['status'], 'pending_review')
                self.assertEqual(item['evidence_refs'], [])
                self.assertTrue(item['reason'])
                self.assertTrue(item['rule_version'])

    def test_game_and_photo_overlap_and_keep_only_safe_traceable_evidence(self):
        self.observation('game', url=('https://example.test/review\n'
                         'javascript:alert(1)\nhttps://example.test/review\n'
                         'https://user:secret@example.test/private'))
        self.observation('photo', 'dxomark_photo', 143)
        game = self.category('gaming')
        photo = self.category('photography')
        self.assertEqual(game['status'], 'evidence_available')
        self.assertEqual(photo['status'], 'evidence_available')
        ref = game['evidence_refs'][0]
        self.assertEqual(ref['run_id'], 'game')
        self.assertEqual(ref['observation_id'], 'obs-game')
        self.assertEqual(ref['metric_key'], 'honor_of_kings_avg_fps')
        self.assertEqual(ref['value'], 59.8)
        self.assertEqual(ref['unit'], 'FPS')
        self.assertEqual(ref['source_id'], 'source-game')
        self.assertEqual(ref['urls'], ['https://example.test/review'])
        self.assertEqual(ref['protocol'], 'review-v1')
        self.assertEqual(json.loads(ref['conditions_json']), {'quality': 'high'})
        self.assertEqual(ref['applicable_scope'], 'model')
        self.assertNotIn('raw_text', ref)
        self.assertNotIn('private', json.dumps(ref))
        self.assertIn('不代表', game['reason'])
        self.assertIn('不代表', photo['reason'])
        self.assertEqual(self.category('gaming', 'M2')['status'], 'pending_review')

    def test_photo_category_accepts_phonearena_photo_subscore(self):
        self.observation('pa', 'phonearena_camera_photo', 156)
        category = self.category('photography')
        self.assertEqual(category['status'], 'evidence_available')
        self.assertEqual(category['evidence_refs'][0]['metric_key'], 'phonearena_camera_photo')

    def test_total_camera_score_and_game_power_do_not_substitute_for_required_metrics(self):
        self.observation('overall', 'dxomark_overall', 160)
        self.observation('pa-overall', 'phonearena_camera_overall', 150)
        self.observation('power', 'honor_of_kings_power_w', 4.5)
        self.assertEqual(self.category('photography')['status'], 'pending_review')
        self.assertEqual(self.category('gaming')['status'], 'pending_review')

    def test_nonpositive_missing_nonfinite_and_unparsed_values_never_cover_a_category(self):
        for index, value in enumerate((None, 0, -1, float('nan'), float('inf'), 'unknown')):
            self.observation('invalid-' + str(index), value=value)
        self.assertEqual(self.category('gaming')['status'], 'pending_review')
        self.assertEqual(self.category('gaming')['evidence_refs'], [])

    def test_missing_reason_blocks_an_inconsistent_positive_observation(self):
        self.observation('missing', missing_reason='not_reported')
        self.assertEqual(self.category('gaming')['status'], 'pending_review')

    def test_absent_or_unsafe_source_never_covers_a_category(self):
        for index, url in enumerate((None, '', 'javascript:alert(1)', 'file:///C:/private/a',
                                     'https://user:pass@example.test/a',
                                     'https://example.test/a b', 'https://[broken')):
            self.observation('unsafe-' + str(index), url=url)
        self.assertEqual(self.category('gaming')['status'], 'pending_review')

    def test_unrelated_or_narrower_scope_never_leaks_into_model_category(self):
        cases = [dict(subject_type='sku', subject_id='S1'),
                 dict(subject_id='M2'), dict(applicable_scope='sku'),
                 dict(applicable_scope=None), dict(variant_id='V1'), dict(sku_id='S1')]
        for index, changes in enumerate(cases):
            self.observation('scope-' + str(index), **changes)
        self.assertEqual(self.category('gaming')['status'], 'pending_review')

    def test_distinct_protocols_remain_separate_references_without_combined_score(self):
        self.observation('photo-v5', 'dxomark_photo', 141, protocol='DXOMARK v5')
        self.observation('photo-v6', 'dxomark_photo', 156, protocol='DXOMARK v6')
        refs = self.category('photography')['evidence_refs']
        self.assertEqual([(r['protocol'], r['value']) for r in refs],
                         [('DXOMARK v5', 141), ('DXOMARK v6', 156)])
        self.assertNotIn('score', self.category('photography'))

    def test_price_and_battery_never_fabricate_senior_suitability(self):
        self.observation('game')
        self.observation('photo', 'dxomark_photo', 143)
        senior = self.category('senior')
        self.assertEqual(senior['status'], 'pending_review')
        self.assertEqual(senior['evidence_refs'], [])
        self.assertIn('适老', senior['reason'])

    def test_view_is_read_only_and_repeat_calls_do_not_share_mutable_evidence(self):
        self.observation('game')
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = self.categories.model_categories(self.repo)
        result['M1'][0]['evidence_refs'][0]['urls'].clear()
        self.assertEqual(self.category('gaming')['evidence_refs'][0]['urls'],
                         ['https://example.test/review'])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_current_snapshot_is_not_mutated_and_every_model_is_represented(self):
        production_db = Path(__file__).resolve().parents[1] / 'data/catalog.sqlite'
        if not production_db.exists():
            self.skipTest('Public snapshot is not shipped in this checkout')
        before = hashlib.sha256(production_db.read_bytes()).hexdigest()
        repo = CatalogRepository(str(production_db))
        result = self.categories.model_categories(repo)
        self.assertEqual(set(result), {r['model_id'] for r in repo.as_records(repo.list_models())})
        for items in result.values():
            self.assertEqual({c['id'] for c in items}, {'gaming', 'senior', 'photography'})
            self.assertEqual(next(c for c in items if c['id'] == 'senior')['status'], 'pending_review')
        self.assertEqual(hashlib.sha256(production_db.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
