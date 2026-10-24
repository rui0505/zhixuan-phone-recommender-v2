"""Independent hand calculations and compatibility tests for experimental ranking."""
import importlib.util
import unittest
from copy import deepcopy

import numpy as np
import pandas as pd
from catalog.adapter import decision_input_from_frame
from engine import rank_decision_input


def tradeoff_input():
    frame = pd.DataFrame({
        'id': ['A', 'B', 'C', 'D'], '产品名称': ['机A', '机B', '机C', '机D'],
        'price': [2000., 3000., 2500., 4000.],
        'weight': [220., 170., 195., 240.],
        'storage': [256., 512., 256., 128.],
        'refresh': [120., 120., 90., 60.], 'screen': [6.1, 6.8, 6.5, 6.9],
    })
    return decision_input_from_frame(frame, {'price': 4, 'weight': 3, 'storage': 2,
                                            'refresh': 1, 'screen': 1})


class CompetitionRankingTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('algorithms'),
                             'experimental algorithms package is not implemented')
        from algorithms.ranking import DecisionProblem, midranks, rrf, todim
        return DecisionProblem, midranks, rrf, todim

    def test_topsis_matches_existing_engine_for_each_sku(self):
        Problem, *_ = self.api()
        di = tradeoff_input()
        problem = Problem(di, target_screen=6.4)
        output = problem.scores(problem.initial_weights)
        baseline = rank_decision_input(di, target_screen=6.4).ranked.set_index('sku_id')
        np.testing.assert_allclose(output['topsis'], baseline.loc[problem.ids, 'score'],
                                   atol=1e-12)

    def test_todim_has_hand_calculated_gain_and_loss(self):
        _, _, _, todim = self.api()
        # w=(.5,.5). A dominates B on one criterion, the other is constant.
        # raw A=sqrt(.5); raw B=-sqrt(2), normalized -> [1,0].
        np.testing.assert_allclose(todim(np.array([[1., 0.], [0., 0.]]),
                                        np.array([.5, .5]), theta=1), [1., 0.])
        # Symmetric exchange is tied, regardless of arbitrary item ordering.
        np.testing.assert_allclose(todim(np.eye(2), np.array([.5, .5])), [.5, .5])

    def test_rrf_fuses_ranks_not_raw_score_and_preserves_ties(self):
        _, ranks, fuse, _ = self.api()
        np.testing.assert_allclose(ranks([.9, .9, .1]), [1.5, 1.5, 3])
        np.testing.assert_allclose(fuse([1., .5, 0], [0., .5, 1], k=1),
                                   [0.375, 1/3, 0.375])
        np.testing.assert_allclose(fuse([10., 5., 0], [0., .5, 1], k=1),
                                   [0.375, 1/3, 0.375])

    def test_cost_direction_and_dominated_product(self):
        Problem, *_ = self.api()
        p = Problem(tradeoff_input(), target_screen=6.5)
        scores = p.scores(p.initial_weights)
        for key in ('topsis', 'todim', 'fusion', 'vikor'):
            self.assertGreater(scores[key][p.ids.index('A')], scores[key][p.ids.index('D')])

    def test_disabled_missing_metric_never_reappears(self):
        Problem, *_ = self.api()
        di = tradeoff_input()
        di['weights']['screen_size_inch'] = 0
        di['X'][0][-1] = None
        di['missing_mask'][0][-1] = True
        p = Problem(di)
        self.assertNotIn('screen_size_inch', p.keys)
        self.assertEqual(len(p.ids), 4)

    def test_rejects_bad_input_and_noncomparable_candidates(self):
        Problem, *_ = self.api()
        di = tradeoff_input()
        for value in (float('nan'), -1, True):
            broken = deepcopy(di)
            broken['weights']['price_cny'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                Problem(broken, target_screen=6.5)
        di['candidates']['model_id'] = 'same-model'
        with self.assertRaises(ValueError):
            Problem(di, target_screen=6.5)

    def test_batch_scores_equal_individual_scores(self):
        Problem, *_ = self.api()
        p = Problem(tradeoff_input(), target_screen=6.5)
        weights = np.array([p.initial_weights, [.4, .1, .2, .2, .1]])
        batch = p.scores(weights)
        for i, w in enumerate(weights):
            for key in batch:
                np.testing.assert_allclose(batch[key][i], p.scores(w)[key])

    def test_zero_and_invalid_algorithm_parameters_rejected(self):
        Problem, _, fuse, todim = self.api()
        p = Problem(tradeoff_input(), target_screen=6.5)
        for w in (np.zeros(5), np.ones(4), [1, 1, 1, 1, float('inf')]):
            with self.assertRaises(ValueError):
                p.scores(w)
        for kwargs in ({'k': 0}, {'alpha': 1.5}, {'theta': 0}):
            with self.assertRaises(ValueError):
                p.scores(p.initial_weights, **kwargs)
        with self.assertRaises(ValueError):
            todim(np.eye(2), [.5, 0])
        with self.assertRaises(ValueError):
            fuse([1, 2], [1])

    def test_input_is_frozen_and_candidates_are_order_independent(self):
        Problem, *_ = self.api()
        di = tradeoff_input()
        p = Problem(di, target_screen=6.5)
        initial = p.scores(p.initial_weights)['fusion'].copy()
        di['X'][0][0] = 999999.
        np.testing.assert_array_equal(p.scores(p.initial_weights)['fusion'], initial)

    def test_vikor_preserves_individual_regret_when_group_utility_ties(self):
        Problem, *_ = self.api()
        frame = pd.DataFrame({'id': ['A', 'B', 'C'], '产品名称': ['机A', '机B', '机C'],
                              'price': [1000, 2000, 3000], 'weight': [240, 200, 160]})
        p = Problem(decision_input_from_frame(frame, {'price': 1, 'weight': 1}))
        np.testing.assert_allclose(p.scores(p.initial_weights)['vikor'], [.5, 1, .5])

    def test_boolean_and_unrepresentable_weights_are_rejected(self):
        Problem, _, _, todim = self.api()
        p = Problem(tradeoff_input(), target_screen=6.5)
        with self.assertRaises(ValueError):
            p.scores([True, 1, 1, 1, 1])
        with self.assertRaises(ValueError):
            todim(np.eye(2), [1e-320, 1e300])

    def test_vikor_exports_q_and_one_minus_q(self):
        Problem, *_ = self.api()
        p = Problem(tradeoff_input(), 6.5)
        scores = p.scores(p.initial_weights)
        np.testing.assert_allclose(scores['vikor_q'], 1-scores['vikor'])

    def test_classic_todim_can_reverse_importance_and_cancel_equal_fusion(self):
        Problem, _, _, _ = self.api()
        frame = pd.DataFrame({'id': ['A', 'B'], '产品名称': ['A', 'B'],
                              'price': [1000, 2000], 'weight': [200, 100]})
        p = Problem(decision_input_from_frame(frame, {'price': 4, 'weight': 1}))
        scores = p.scores([.8, .2], theta=1, alpha=.5)
        np.testing.assert_allclose(scores['topsis'], [.8, .2])
        np.testing.assert_allclose(scores['todim'], [0, 1])
        self.assertAlmostEqual(scores['fusion'][0], scores['fusion'][1])


if __name__ == '__main__':
    unittest.main()
