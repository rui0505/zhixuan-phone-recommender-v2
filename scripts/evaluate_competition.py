"""Deterministic synthetic ablations, with held-out pair labels never queried.

Run from the repository:
python scripts/evaluate_competition.py --users 24 --output local_evidence/competition.json
"""
import argparse
from collections import defaultdict
from itertools import combinations
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithms import VERSION
from algorithms.preference import PreferenceSession, SessionConfig
from algorithms.ranking import DecisionProblem, midranks
from catalog.adapter import build_input
from catalog.release import ReleaseStore


class EvaluationSession(PreferenceSession):
    """Experiment-only restriction; the application's session implementation is unchanged."""
    def __init__(self, problem, config, *, holdout):
        super().__init__(problem, config)
        self.holdout = set(holdout)

    def question_pool(self):
        return [pair for pair in super().question_pool() if pair not in self.holdout]


def split_pairs(problem, seed):
    pairs = [pair for pair in combinations(range(len(problem.ids)), 2)
             if problem.models[pair[0]] != problem.models[pair[1]]
             and not np.allclose(problem.utility[pair[0]], problem.utility[pair[1]],
                                 atol=1e-12, rtol=0)]
    rng = np.random.default_rng(seed)
    rng.shuffle(pairs)
    count = max(1, int(.3*len(pairs))) if pairs else 0
    return pairs[count:], pairs[:count]


def metrics(scores, truth, pairs, *, utility=None):
    scores, truth = np.asarray(scores, dtype=float), np.asarray(truth, dtype=float)
    correct = []
    tradeoffs = []
    for i, j in pairs:
        if abs(truth[i]-truth[j]) <= 1e-12:
            continue
        diff = scores[i]-scores[j]
        value = .5 if abs(diff) <= 1e-12 else float(diff*(truth[i]-truth[j]) > 0)
        correct.append(value)
        if utility is not None:
            left, right = utility[[i, j]]
            if not (np.all(left >= right-1e-12) or np.all(right >= left-1e-12)):
                tradeoffs.append(value)
    first = np.isclose(scores, scores.max(), rtol=0, atol=1e-12)
    span = float(np.ptp(truth))
    regret = (truth.max()-truth[first].mean())/span if span > 1e-12 else 0.
    # Average gain within each predicted tie, avoiding SKU ordering advantages.
    rank = midranks(scores)
    gains = np.exp2(truth)-1
    tied_gain = np.zeros(len(gains))
    for value in np.unique(rank):
        group = rank == value
        tied_gain[group] = gains[group].mean()
    top = np.argsort(rank, kind='stable')[:5]
    discounts = np.log2(np.arange(len(top))+2)
    ideal = np.sort(gains)[::-1][:len(top)] @ (1/discounts)
    ndcg = (tied_gain[top] @ (1/discounts))/ideal if ideal > 1e-12 else 1.
    return {'pairwise_accuracy': float(np.mean(correct)) if correct else None,
            'tradeoff_accuracy': float(np.mean(tradeoffs)) if tradeoffs else None,
            'tradeoff_test_pairs': len(tradeoffs),
            'test_pairs': len(correct), 'top1_regret': float(regret), 'ndcg5': float(ndcg)}


def latent_utility(problem, weight, family):
    u = problem.utility
    if family == 'linear':
        return u @ weight
    if family == 'bottleneck':
        important = (weight >= np.median(weight)) & (np.ptp(u, axis=0) > 1e-12)
        shortfall = u[:, important].min(axis=1) if important.any() else np.zeros(len(u))
        return .7*(np.sqrt(u) @ weight) + .3*shortfall
    raise ValueError('unknown simulated utility family')


def paired_interval(values, reference, *, seed, samples=2000):
    """Percentile bootstrap over paired users; conditional on this frozen catalog.

    Positive delta means higher metric value, including regret (where lower is better).
    These exploratory intervals do not apply a multiple-comparison correction.
    """
    if not values or set(values) != set(reference):
        raise ValueError('paired comparison requires the same nonempty user IDs')
    delta = np.array([values[u]-reference[u] for u in sorted(values)], dtype=float)
    draws = np.random.default_rng(seed).integers(len(delta), size=(samples, len(delta)))
    boot = delta[draws].mean(axis=1)
    low, high = np.quantile(boot, [.025, .975])
    return dict(users=len(delta), delta=float(delta.mean()),
                ci95_low=float(low), ci95_high=float(high))


def comparisons(runs, seed):
    result = []
    pairs = [('fixed_fusion', 'fixed_topsis'), ('prior_fusion', 'fixed_fusion'),
             ('learn_information', 'prior_fusion'),
             ('learn_disagreement', 'learn_information'),
             ('learn_information', 'learn_random'),
             ('learn_information_topsis', 'prior_topsis'),
             ('learn_information_vikor', 'prior_vikor'),
             ('learn_information_utility', 'prior_utility'),
             ('learn_information', 'learn_information_topsis'),
             ('learn_information', 'learn_information_vikor')]
    for family in ('linear', 'bottleneck'):
        for method, reference in pairs:
            for metric in ('pairwise_accuracy', 'tradeoff_accuracy', 'top1_regret', 'ndcg5'):
                def collect(name):
                    return {r['user']: r[metric] for r in runs if r['utility'] == family
                            and r['method'] == name and r[metric] is not None}
                left, right = collect(method), collect(reference)
                if left and set(left) == set(right):
                    result.append(dict(utility=family, method=method, reference=reference,
                                       metric=metric, **paired_interval(left, right, seed=seed)))
    return result


def evaluate(problem, *, users=24, budget=5, particles=128, seed=20261020,
             theta=1, alpha=.5, prior_strength=12):
    if type(users) is not int or not 1 <= users <= 1000:
        raise ValueError('users must be an integer in [1,1000]')
    config = SessionConfig(particles=particles, seed=seed, max_questions=budget,
                           theta=theta, alpha=alpha, prior_strength=prior_strength)
    fixed = problem.scores(problem.initial_weights, theta=theta, alpha=alpha, k=config.k)
    fixed = {key: fixed[key] for key in ('topsis', 'todim', 'fusion', 'vikor')}
    fixed['utility'] = problem.utility @ problem.initial_weights
    runs = []
    for family in ('linear', 'bottleneck'):
        for user in range(users):
            children = np.random.SeedSequence([seed, user]).spawn(4)
            streams = {key: int(child.generate_state(1)[0])
                       for key, child in zip(('truth', 'learner', 'split', 'noise'), children)}
            rng = np.random.default_rng(streams['truth'])
            true_weights = rng.dirichlet(np.ones(len(problem.keys)))
            truth = latent_utility(problem, true_weights, family)
            _, holdout = split_pairs(problem, streams['split'])
            if not holdout:
                raise ValueError('当前候选没有可用于偏好评测的不同机型对')
            entries = {f'fixed_{key}': (value, [], {}) for key, value in fixed.items()}
            learner_config = {**config.__dict__, 'seed': streams['learner']}
            prior_session = PreferenceSession(problem, SessionConfig(**learner_config))
            for method in fixed:
                entries['prior_'+method] = (prior_session.posterior @ prior_session._scores[method],
                                            [], {})
            for strategy in ('random', 'information', 'disagreement'):
                per_user = SessionConfig(**{**learner_config, 'strategy': strategy})
                s = EvaluationSession(problem, per_user, holdout=holdout)
                for _ in range(budget):
                    question = s.question()
                    if question is None:
                        break
                    i, j = [problem.ids.index(question[key]) for key in ('a', 'b')]
                    # Common random numbers: same user and pair -> same noisy answer in every policy.
                    noise_rng = np.random.default_rng(np.random.SeedSequence([streams['noise'], i, j]))
                    z = np.clip((truth[i]-truth[j])/.1, -50, 50)
                    probability = .05+.9/(1+np.exp(-z))
                    s.answer(question['id'], 'a' if noise_rng.random() < probability else 'b')
                for method in fixed:
                    name = 'learn_'+strategy+('' if method == 'fusion' else '_'+method)
                    entries[name] = (s.posterior @ s._scores[method], s.history,
                                     dict(zip(problem.keys, s.mean_weights.tolist())))
            for method, (scores, history, learned) in entries.items():
                runs.append({'utility': family, 'user': user, 'method': method,
                             'questions': len(history),
                             **metrics(scores, truth, holdout, utility=problem.utility),
                             'random_streams': streams,
                             'true_weights': true_weights.tolist(), 'learned_weights': learned,
                             'history': history,
                             'holdout_pairs': [[problem.ids[i], problem.ids[j]] for i, j in holdout]})
    grouped = defaultdict(list)
    for run in runs:
        grouped[(run['utility'], run['method'])].append(run)
    summary = []
    for (family, method), group in sorted(grouped.items()):
        row = {'utility': family, 'method': method, 'users': len(group)}
        for name in ('pairwise_accuracy', 'tradeoff_accuracy', 'top1_regret', 'ndcg5', 'questions'):
            values = np.asarray([run[name] for run in group if run[name] is not None], dtype=float)
            row[name] = float(values.mean()) if len(values) else None
            row[name+'_se'] = float(values.std(ddof=1)/np.sqrt(len(values))) if len(values)>1 else None
        summary.append(row)
    return {'algorithm_version': VERSION, 'synthetic_only': True,
            'decision_sha256': problem.decision_sha256, 'dataset_version': problem.dataset_version,
            'candidate_count': len(problem.ids), 'active_metrics': problem.keys,
            'config': config.__dict__, 'users_per_utility': users,
            'evaluation': 'fixed 30% pair holdout per user; excluded from all query policies; '
                          'latent-utility metrics are simulated, not real user effectiveness',
            'comparison_note': 'Exploratory paired user bootstrap, 2000 draws, no multiplicity '
                               'correction; delta=method-reference; regret lower is better. '
                               'Every learned output within a policy shares the same answers.',
            'paired_comparisons': comparisons(runs, seed), 'summary': summary, 'runs': runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--users', type=int, default=24)
    parser.add_argument('--budget', type=int, default=5)
    parser.add_argument('--particles', type=int, default=128)
    parser.add_argument('--seed', type=int, default=20261020)
    parser.add_argument('--theta', type=float, default=1.)
    parser.add_argument('--alpha', type=float, default=.5)
    parser.add_argument('--prior-strength', type=float, default=12.)
    parser.add_argument('--output', type=Path, default=Path('local_evidence/competition.json'))
    args = parser.parse_args()
    release = ReleaseStore(ROOT/'data/catalog.sqlite').get()
    di = build_input(release.repo, {'budget': {'min': 1000, 'max': 6000}, 'min_storage': 256},
                     {'price': 5, 'weight': 2, 'storage': 2, 'refresh': 1}, None, 'new')
    problem = DecisionProblem(di)
    result = evaluate(problem, **{k: v for k, v in vars(args).items() if k != 'output'})
    result['release'] = release.key
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
                           encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'candidate_count': len(problem.ids),
                      'summary': result['summary']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
