"""Final review regressions use synthetic data; never change supplied originals."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import json
import tempfile
import unittest
from catalog_fixture import create_catalog
from catalog.repository import CatalogRepository
from catalog.adapter import build_input
from catalog.evidence import build_evidence, render_template
from engine import rank_decision_input
from ui.export_results import export_results


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.con = create_catalog(Path(self.temp.name) / 'fixture.sqlite')
        self.repo = CatalogRepository(str(Path(self.temp.name) / 'fixture.sqlite'))

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def price_input(self):
        return self.repo.build_decision_input(['price_cny'])

    def test_old_score_cannot_bind_to_new_facts_under_same_version_labels(self):
        old = rank_decision_input(self.price_input())
        self.con.execute("UPDATE price_snapshots SET amount=3000 WHERE sku_id='S1'")
        self.con.commit()
        with self.assertRaises(ValueError):
            build_evidence(self.price_input(), old, {})

    def test_old_score_cannot_export_with_new_evidence(self):
        old = rank_decision_input(self.price_input())
        self.con.execute("UPDATE price_snapshots SET amount=3000 WHERE sku_id='S1'")
        self.con.commit()
        new_input = self.price_input()
        bundle = build_evidence(new_input, rank_decision_input(new_input), {})
        with self.assertRaises(ValueError):
            export_results(old, bundle, 'json')

    def test_mutated_result_score_cannot_export(self):
        di = self.price_input()
        result = rank_decision_input(di)
        bundle = build_evidence(di, result, {})
        result.ranked.loc[result.ranked.index[0], 'score'] = 0.123
        with self.assertRaises(ValueError):
            export_results(result, bundle, 'csv')

    def test_changed_weight_cannot_bind_to_previous_ranking(self):
        di = self.price_input()
        result = rank_decision_input(di)
        changed = deepcopy(di)
        changed['weights']['price_cny'] = 2
        with self.assertRaises(ValueError):
            build_evidence(changed, result, {})

    def test_screen_target_and_transform_are_in_evidence_and_exports(self):
        di = build_input(self.repo, {}, {'screen': 1}, 6.5, 'new')
        result = rank_decision_input(di, target_screen=6.5)
        bundle = build_evidence(di, result, {})
        self.assertEqual(bundle['decision_context']['target_screen'], 6.5)
        fact = next(iter(bundle['facts'].values()))
        self.assertAlmostEqual(fact['transformation']['deviation'], 0.2)
        self.assertEqual(fact['transformation']['direction'], 'cost')
        self.assertIn('目标 6.5', render_template(bundle))
        data = json.loads(export_results(result, bundle, 'json'))
        self.assertEqual(data['ranking'][0]['target_screen'], 6.5)
        from openpyxl import load_workbook
        wb = load_workbook(BytesIO(export_results(result, bundle, 'xlsx')))
        self.assertTrue(any(row[0] == 'target_screen' and row[1] == 6.5
                            for row in wb['证据与说明'].values))
        wb.close()

    def test_inconsistent_screen_targets_are_rejected(self):
        di = build_input(self.repo, {}, {'screen': 1}, 6.5, 'new')
        with self.assertRaises(ValueError):
            rank_decision_input(di, target_screen=7.0)

    def test_single_model_remains_visible_without_a_score(self):
        di = self.repo.build_decision_input(['price_cny'], min_price=1000, max_price=1000)
        result = rank_decision_input(di)
        self.assertEqual(result.status, 'insufficient_comparable_data')
        self.assertTrue(result.ranked.empty)
        self.assertEqual(result.unscored_candidates['sku_id'].tolist(), ['S1'])
        self.assertNotIn('score', result.unscored_candidates)

    def test_source_links_are_split_then_validated_individually(self):
        from ui.catalog_views import safe_url, safe_urls
        raw = 'https://example.com/a\nhttps://example.org/b\nhttps://example.com/a'
        self.assertIsNone(safe_url(raw))
        self.assertEqual(safe_urls(raw), ['https://example.com/a', 'https://example.org/b'])
        self.assertEqual(safe_urls('https://user:password@example.com/\njavascript:alert(1)\nhttps://example.com/ok'),
                         ['https://example.com/ok'])
