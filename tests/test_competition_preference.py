import importlib.util
import json
import unittest

import numpy as np
import pandas as pd
from catalog.adapter import decision_input_from_frame
from algorithms.ranking import DecisionProblem
from test_competition_ranking import tradeoff_input


class PreferenceTests(unittest.TestCase):
    def session(self, problem=None, **config):
        self.assertIsNotNone(importlib.util.find_spec('algorithms.preference'),
                             'preference learning is not implemented')
        from algorithms.preference import PreferenceSession, SessionConfig
        return PreferenceSession(problem or DecisionProblem(tradeoff_input(), 6.5),
                                 SessionConfig(**config))

    def two_tradeoffs(self):
        frame = pd.DataFrame({'id': ['A', 'B'], '产品名称': ['机A', '机B'],
                              'price': [1000, 2000], 'weight': [220, 160]})
        return DecisionProblem(decision_input_from_frame(frame, {'price': 1, 'weight': 1}))

    def test_answer_learns_towards_observed_choice(self):
        s = self.session(self.two_tradeoffs())
        before = s.mean_weights.copy()
        q = s.question()
        s.answer(q['id'], 'a')
        after = s.mean_weights
        chosen = s.problem.ids.index(q['a'])
        attribute = int(np.argmax(s.problem.utility[chosen]))
        self.assertGreater(after[attribute], before[attribute])
        self.assertAlmostEqual(float(s.posterior.sum()), 1)
        self.assertTrue(np.isfinite(s.posterior).all())

    def test_skip_and_indifference_preserve_prior_but_consume_question(self):
        for answer in ('skip', 'indifferent'):
            s = self.session()
            before = s.posterior.copy()
            q = s.question()
            s.answer(q['id'], answer)
            np.testing.assert_array_equal(before, s.posterior)
            self.assertEqual(len(s.history), 1)
            next_q = s.question()
            self.assertTrue(next_q is None or next_q['id'] != q['id'])

    def test_undo_replays_from_prior_and_duplicate_submission_fails(self):
        s = self.session()
        q = s.question()
        original = s.report()['session_sha256']
        s.answer(q['id'], 'a')
        with self.assertRaises(ValueError):
            s.answer(q['id'], 'a')
        s.undo()
        self.assertEqual(s.report()['session_sha256'], original)
        self.assertEqual(s.question()['id'], q['id'])

    def test_questions_exclude_dominated_identical_and_same_model(self):
        s = self.session()
        for pair in s.question_pool():
            i, j = pair
            self.assertNotEqual(s.problem.models[i], s.problem.models[j])
            left, right = s.problem.utility[[i, j]]
            self.assertFalse(np.all(left >= right))
            self.assertFalse(np.all(right >= left))
        frame = pd.DataFrame({'id': ['A', 'B', 'C'], '产品名称': ['same', 'same', 'other'],
                              'price': [1000, 2000, 4000], 'weight': [220, 160, 250]})
        problem = DecisionProblem(decision_input_from_frame(frame, {'price': 1, 'weight': 1}))
        self.assertIsNone(self.session(problem).question())

    def test_stability_splits_tied_first_place(self):
        frame = pd.DataFrame({'id': ['A', 'B'], '产品名称': ['机A', '机B'], 'price': [1000, 1000]})
        s = self.session(DecisionProblem(decision_input_from_frame(frame, {'price': 1})))
        report = s.report()
        self.assertIsNone(s.question())
        for row in report['ranking']:
            self.assertAlmostEqual(row['top1_support'], .5)
            self.assertAlmostEqual(row['mean_rank'], 1.5)
        self.assertTrue(report['experimental'])
        json.dumps(report, ensure_ascii=False, allow_nan=False)

    def test_invalid_config_and_answers_are_rejected_without_mutation(self):
        for config in ({'particles': 0}, {'particles': True}, {'strategy': 'bogus'},
                       {'temperature': 0}, {'noise': 1}, {'seed': -1},
                       {'max_questions': 99}, {'alpha': float('nan')}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.session(**config)
        s = self.session()
        digest = s.report()['session_sha256']
        with self.assertRaises(ValueError):
            s.answer('not-issued', 'a')
        with self.assertRaises(ValueError):
            s.answer(s.question()['id'], 'unknown')
        self.assertEqual(digest, s.report()['session_sha256'])

    def test_replay_validates_fingerprint_and_preserves_results(self):
        s = self.session()
        q = s.question()
        s.answer(q['id'], 'b')
        report = s.report()
        from algorithms.preference import PreferenceSession
        restored = PreferenceSession.replay(s.problem, report)
        self.assertEqual(restored.report(), report)
        altered = dict(report, decision_sha256='0'*64)
        with self.assertRaises(ValueError):
            PreferenceSession.replay(s.problem, altered)

    def test_max_questions_stops_and_strategies_share_pool(self):
        sessions = [self.session(strategy=method, max_questions=1)
                    for method in ('random', 'information', 'disagreement')]
        self.assertEqual(sessions[0].question_pool(), sessions[1].question_pool())
        for s in sessions:
            q = s.question()
            self.assertGreaterEqual(q['information_gain'], 0)
            s.answer(q['id'], 'a')
            self.assertIsNone(s.question())

    def test_seed_reproducibility_and_prior_config_are_in_fingerprint(self):
        a, b = self.session(), self.session()
        self.assertEqual(a.report(), b.report())
        c = self.session(prior_strength=2)
        self.assertNotEqual(a.report()['session_sha256'], c.report()['session_sha256'])

    def test_each_ranking_method_has_its_own_stability_and_weight_intervals(self):
        s = self.session()
        report = s.report()
        for method in ('topsis', 'todim', 'fusion', 'vikor', 'utility'):
            self.assertAlmostEqual(sum(row['stability'][method]['top1_support']
                                       for row in report['ranking']), 1)
        for row in report['weight_summary']:
            self.assertLessEqual(row['p05'], row['p95'])
            self.assertTrue(0 <= row['p05'] <= row['p95'] <= 1)
        q = s.question()
        s.answer(q['id'], 'a')
        current = s.report()
        self.assertTrue(0 < current['effective_particles'] <= s.config.particles+1e-8)
        self.assertIn('posterior_is_concentrated', current)


if __name__ == '__main__':
    unittest.main()
