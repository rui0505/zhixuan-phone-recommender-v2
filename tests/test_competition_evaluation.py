import importlib.util
import unittest

import numpy as np
import pandas as pd
from catalog.adapter import decision_input_from_frame
from algorithms.preference import PreferenceSession, SessionConfig
from algorithms.ranking import DecisionProblem
from test_competition_ranking import tradeoff_input


class EvaluationTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.evaluate_competition'),
                             'ablation evaluator is not implemented')
        from scripts.evaluate_competition import evaluate, metrics, split_pairs, EvaluationSession
        return evaluate, metrics, split_pairs, EvaluationSession

    def test_perfect_reversed_and_tied_rankings_have_expected_metrics(self):
        _, metrics, *_ = self.api()
        pairs = [(0, 1), (0, 2), (1, 2)]
        true = np.array([1., .5, 0.])
        perfect = metrics(true, true, pairs)
        self.assertEqual(perfect['pairwise_accuracy'], 1)
        self.assertEqual(perfect['top1_regret'], 0)
        self.assertAlmostEqual(perfect['ndcg5'], 1)
        reverse = metrics(-true, true, pairs)
        self.assertEqual(reverse['pairwise_accuracy'], 0)
        self.assertEqual(reverse['top1_regret'], 1)
        tied = metrics(np.ones(3), true, pairs)
        self.assertEqual(tied['pairwise_accuracy'], .5)
        self.assertAlmostEqual(tied['top1_regret'], .5)

    def test_holdout_pairs_never_enter_questions(self):
        _, _, split, Session = self.api()
        p = DecisionProblem(tradeoff_input(), 6.5)
        train, holdout = split(p, 2026)
        self.assertTrue(holdout)
        self.assertFalse(set(train) & set(holdout))
        s = Session(p, SessionConfig(), holdout=holdout)
        for _ in range(5):
            q = s.question()
            if q is None:
                break
            pair = tuple(sorted((p.ids.index(q['a']), p.ids.index(q['b']))))
            self.assertNotIn(pair, holdout)
            s.answer(q['id'], 'a')

    def test_evaluation_is_deterministic_and_labels_synthetic(self):
        evaluate, *_ = self.api()
        p = DecisionProblem(tradeoff_input(), 6.5)
        options = dict(users=2, budget=2, particles=32, seed=2026)
        result = evaluate(p, **options)
        self.assertEqual(result, evaluate(p, **options))
        self.assertTrue(result['synthetic_only'])
        self.assertEqual(len(result['runs']), 4*25)  # fixed/prior + five outputs per policy
        for run in result['runs']:
            self.assertLessEqual(run['questions'], 2)
            self.assertTrue(0 <= run['pairwise_accuracy'] <= 1)
            self.assertTrue(0 <= run['ndcg5'] <= 1.0000001)
        self.assertEqual(len(result['summary']), 50)
        self.assertFalse(any(row['method'].endswith('vikor_q') for row in result['runs']))

    def test_no_answer_learning_equals_same_user_prior(self):
        evaluate, *_ = self.api()
        # The only pair is held out, so no policy can learn anything.
        frame = pd.DataFrame({'id': ['A', 'B'], '产品名称': ['A', 'B'],
                              'price': [1000, 2000], 'weight': [200, 100]})
        p = DecisionProblem(decision_input_from_frame(frame, {'price': 1, 'weight': 1}))
        result = evaluate(p, users=8, budget=1, particles=16, seed=12, alpha=.7)
        priors = {(r['utility'], r['user']): r for r in result['runs']
                  if r['method'] == 'prior_fusion'}
        for row in result['runs']:
            if row['method'] in ('learn_random', 'learn_information', 'learn_disagreement'):
                self.assertEqual(row['questions'], 0)
                prior = priors[(row['utility'], row['user'])]
                for key in ('ndcg5', 'top1_regret', 'pairwise_accuracy'):
                    self.assertAlmostEqual(row[key], prior[key])

    def test_truth_randomness_is_independent_of_learner_particles(self):
        evaluate, *_ = self.api()
        p = DecisionProblem(tradeoff_input(), 6.5)
        result = evaluate(p, users=2, budget=1, particles=16, seed=17, prior_strength=0)
        for row in result['runs']:
            self.assertIn('random_streams', row, 'independent simulation streams must be auditable')
            streams = row['random_streams']
            self.assertEqual(len(set(streams.values())), 4)
            particle0 = np.random.default_rng(streams['learner']).dirichlet(
                np.ones(len(p.keys)), 16)[0]
            self.assertGreater(float(np.linalg.norm(particle0-row['true_weights'])), 1e-8)

    def test_learning_baselines_share_identical_questions(self):
        evaluate, *_ = self.api()
        p = DecisionProblem(tradeoff_input(), 6.5)
        result = evaluate(p, users=2, budget=2, particles=16, seed=7)
        rows = {(r['utility'], r['user'], r['method']): r for r in result['runs']}
        for family in ('linear', 'bottleneck'):
            for user in range(2):
                for strategy in ('random', 'information', 'disagreement'):
                    base = rows[family, user, 'learn_'+strategy]
                    for method in ('topsis', 'todim', 'vikor', 'utility'):
                        row = rows[family, user, 'learn_'+strategy+'_'+method]
                        self.assertEqual(row['history'], base['history'])
                        self.assertEqual(row['learned_weights'], base['learned_weights'])

    def test_paired_interval_uses_user_differences_and_rejects_incomplete_pairing(self):
        from scripts.evaluate_competition import paired_interval
        a = {0: .4, 1: .9, 2: .7}
        b = {0: .3, 1: .8, 2: .6}
        out = paired_interval(a, b, seed=9)
        self.assertAlmostEqual(out['delta'], .1)
        self.assertAlmostEqual(out['ci95_low'], .1)
        self.assertAlmostEqual(out['ci95_high'], .1)
        with self.assertRaises(ValueError):
            paired_interval(a, {0: .3}, seed=9)

    def test_tradeoff_accuracy_excludes_easy_dominated_pairs(self):
        _, metrics, *_ = self.api()
        # A/B is a genuine tradeoff; both dominate C. Only A/B is misranked.
        u = np.array([[1., .5], [.5, 1.], [0., 0.]])
        out = metrics(np.array([.8, .9, .1]), np.array([1., .9, 0.]),
                      [(0, 1), (0, 2), (1, 2)], utility=u)
        self.assertAlmostEqual(out['pairwise_accuracy'], 2/3)
        self.assertEqual(out['tradeoff_accuracy'], 0)
        self.assertEqual(out['tradeoff_test_pairs'], 1)


if __name__ == '__main__':
    unittest.main()
