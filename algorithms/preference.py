"""Finite-particle Bayesian preferences and reproducible active elicitation.

The response model is additive utility on frozen benefit-oriented features.
Ranking experts are separate hypotheses; neither supplies ground-truth labels.
"""
from dataclasses import asdict, dataclass
from copy import deepcopy
from itertools import combinations
import hashlib
import json

import numpy as np

from . import VERSION
from .ranking import DecisionProblem, midranks, parameter


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class SessionConfig:
    particles: int = 256
    seed: int = 20261020
    prior_strength: float = 12
    temperature: float = .12
    noise: float = .1
    theta: float = 1
    alpha: float = .5
    k: float = 10
    disagreement_bonus: float = .5
    max_questions: int = 5
    strategy: str = 'disagreement'

    def __post_init__(self):
        for name, low, high in (('particles', 16, 1024), ('seed', 0, 2**32-1),
                                 ('max_questions', 1, 10)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{name} 必须为 {low}—{high} 的整数')
        parameter(self.prior_strength, 'prior_strength', maximum=100)
        parameter(self.temperature, 'temperature', minimum=.01, maximum=2)
        parameter(self.noise, 'noise', maximum=.5)
        parameter(self.theta, 'theta', minimum=.05, maximum=20)
        parameter(self.alpha, 'alpha', maximum=1)
        parameter(self.k, 'k', minimum=.1, maximum=100)
        parameter(self.disagreement_bonus, 'disagreement_bonus', maximum=2)
        if self.strategy not in ('random', 'information', 'disagreement'):
            raise ValueError('未知选题策略')


def entropy(probability):
    p = np.clip(probability, 1e-15, 1-1e-15)
    return -(p*np.log(p)+(1-p)*np.log1p(-p))


class PreferenceSession:
    def __init__(self, problem: DecisionProblem, config=None):
        self.problem = problem
        self.config = config or SessionConfig()
        rng = np.random.default_rng(self.config.seed)
        self.particles = rng.dirichlet(1+self.config.prior_strength*problem.initial_weights,
                                      self.config.particles)
        self.particles.setflags(write=False)
        self.posterior = np.full(self.config.particles, 1/self.config.particles)
        self._history = []
        self._scores = problem.scores(self.particles, theta=self.config.theta,
                                     alpha=self.config.alpha, k=self.config.k)
        self._ranks = midranks(self._scores['fusion'])
        self._utility = self.particles @ problem.utility.T
        self._scores['utility'] = self._utility
        self._score_ranks = {key: midranks(self._scores[key])
                             for key in ('topsis', 'todim', 'fusion', 'vikor', 'utility')}
        self._initial_scores = problem.scores(problem.initial_weights, theta=self.config.theta,
                                             alpha=self.config.alpha, k=self.config.k)
        self._base_identity = {'algorithm_version': VERSION,
                               'decision_sha256': problem.decision_sha256,
                               'config': asdict(self.config)}

    @property
    def history(self):
        return deepcopy(self._history)

    @property
    def mean_weights(self):
        return self.posterior @ self.particles

    def choice_probability(self, i, j):
        z = np.clip((self._utility[:, i]-self._utility[:, j])/self.config.temperature, -50, 50)
        return self.config.noise/2 + (1-self.config.noise)/(1+np.exp(-z))

    def question_pool(self):
        asked = {tuple(sorted((h['a'], h['b']))) for h in self._history}
        shortlist = set()
        for key in ('topsis', 'todim', 'fusion'):
            score = self.posterior @ self._scores[key]
            models = set()
            for i in sorted(range(len(score)), key=lambda i: (-score[i], self.problem.ids[i])):
                if self.problem.models[i] in models:
                    continue
                shortlist.add(i)
                models.add(self.problem.models[i])
                if len(models) == 8:
                    break
        pairs = []
        for i, j in combinations(sorted(shortlist), 2):
            if (self.problem.models[i] == self.problem.models[j]
                    or tuple(sorted((self.problem.ids[i], self.problem.ids[j]))) in asked):
                continue
            left, right = self.problem.utility[[i, j]]
            if np.all(left >= right-1e-12) or np.all(right >= left-1e-12):
                continue
            pairs.append((i, j))
        return pairs

    def question(self):
        if len(self._history) >= self.config.max_questions:
            return None
        choices = []
        for i, j in self.question_pool():
            probability = self.choice_probability(i, j)
            predictive = float(self.posterior @ probability)
            ig = max(0., float(entropy(predictive)-self.posterior @ entropy(probability)))
            if ig < 1e-9:
                continue
            t = self._scores['topsis'][:, i] - self._scores['topsis'][:, j]
            d = self._scores['todim'][:, i] - self._scores['todim'][:, j]
            disagree = ((t > 1e-12) & (d < -1e-12)) | ((t < -1e-12) & (d > 1e-12))
            disagreement = float(self.posterior @ disagree)
            value = ig * (1+self.config.disagreement_bonus*disagreement)
            if self.config.strategy == 'information':
                value = ig
            a, b = self.problem.ids[i], self.problem.ids[j]
            choices.append({'id': digest({**self._base_identity, 'pair': [a, b]}),
                            'a': a, 'b': b, 'information_gain': ig,
                            'disagreement': disagreement, 'selection_value': value,
                            'predicted_a': predictive})
        if not choices:
            return None
        if self.config.strategy == 'random':
            rng = np.random.default_rng(self.config.seed+len(self._history))
            return choices[int(rng.integers(len(choices)))]
        return max(choices, key=lambda c: (c['selection_value'], c['id']))

    def _update(self, record):
        if record['answer'] not in ('a', 'b'):
            return
        i, j = [self.problem.ids.index(record[key]) for key in ('a', 'b')]
        probability = self.choice_probability(i, j)
        likelihood = probability if record['answer'] == 'a' else 1-probability
        log_mass = np.log(np.maximum(self.posterior, np.finfo(float).tiny)) + np.log(likelihood)
        mass = np.exp(log_mass-log_mass.max())
        self.posterior = mass/mass.sum()

    def answer(self, question_id, answer):
        if answer not in ('a', 'b', 'skip', 'indifferent'):
            raise ValueError('回答必须为 a、b、skip 或 indifferent')
        question = self.question()
        if question is None or question['id'] != question_id:
            raise ValueError('问题过期、重复提交或并非当前问题')
        record = {**question, 'answer': answer}
        self._update(record)
        self._history.append(record)

    def undo(self):
        if not self._history:
            return
        self._history.pop()
        self.posterior = np.full(self.config.particles, 1/self.config.particles)
        for record in self._history:
            self._update(record)

    def report(self):
        expected = {key: self.posterior @ scores for key, scores in self._scores.items()}
        stability = {}
        for key, ranks in self._score_ranks.items():
            mean = self.posterior @ ranks
            sd = np.sqrt(np.maximum(0, self.posterior @ ranks**2-mean**2))
            first = ranks == ranks.min(axis=1, keepdims=True)
            stability[key] = (mean, sd, self.posterior @ (first/first.sum(axis=1, keepdims=True)))
        mean_rank = self.posterior @ self._ranks
        std = np.sqrt(np.maximum(0, self.posterior @ self._ranks**2 - mean_rank**2))
        first = np.isclose(self._scores['fusion'],
                           self._scores['fusion'].max(axis=1, keepdims=True),
                           atol=1e-12, rtol=0)
        support = self.posterior @ (first/first.sum(axis=1, keepdims=True))
        ranking = []
        for i, sku in enumerate(self.problem.ids):
            row = {'sku_id': sku, 'model_id': self.problem.models[i],
                   'baseline_topsis': float(self.problem.baseline[i]),
                   **{key: float(score[i]) for key, score in expected.items()},
                   'stability': {key: dict(mean_rank=float(values[0][i]),
                                           rank_std=float(values[1][i]),
                                           top1_support=float(values[2][i]))
                                 for key, values in stability.items()},
                   'mean_rank': float(mean_rank[i]), 'rank_std': float(std[i]),
                   'top1_support': float(support[i])}
            ranking.append(row)
        ranking.sort(key=lambda r: (-round(r['fusion'], 12), r['sku_id']))
        identity = {**self._base_identity, 'history': self.history}
        weight_summary = []
        for j, key in enumerate(self.problem.keys):
            order = np.argsort(self.particles[:, j])
            cumulative = np.cumsum(self.posterior[order])
            quantiles = [float(self.particles[order[min(np.searchsorted(cumulative, q),
                                                      len(order)-1)], j]) for q in (.05, .95)]
            weight_summary.append(dict(metric=key, initial=float(self.problem.initial_weights[j]),
                                       mean=float(self.mean_weights[j]),
                                       p05=quantiles[0], p95=quantiles[1]))
        effective = float(1/np.sum(self.posterior**2))
        return {
            **identity, 'session_sha256': digest(identity), 'experimental': True,
            'dataset_version': self.problem.dataset_version,
            'feature_rule_version': self.problem.feature_rule_version,
            'target_screen': self.problem.target_screen,
            'selection_rules': deepcopy(self.problem.selection_rules),
            'candidate_ids': list(self.problem.ids),
            'initial_weights': dict(zip(self.problem.keys, self.problem.initial_weights.tolist())),
            'learned_weights': dict(zip(self.problem.keys, self.mean_weights.tolist())),
            'weight_summary': weight_summary,
            'effective_particles': effective,
            'posterior_is_concentrated': effective < .1*self.config.particles,
            'ranking': ranking,
            'limitations': ['试验模型，未验证真实用户效果；参数不是心理测量',
                            '支持率是在当前粒子先验和回答模型下的统计量，不是满意概率',
                            '同分以 ID 排列仅供展示；VIKOR 仅比较 Q 指数',
                            '排名依赖冻结候选集；改变条件须重新开始',
                            '经典 TODIM 的损失项含逆权重，可能反向并抵消 TOPSIS；融合不是默认优选',
                            '权重区间是有限粒子后验的分位数，不是真实用户参数的置信保证',
                            '无倾向/跳过不更新；加性回答模型与排序融合的差异须实验检验'],
        }

    @classmethod
    def replay(cls, problem, report):
        if (report.get('decision_sha256') != problem.decision_sha256
                or report.get('algorithm_version') != VERSION):
            raise ValueError('数据/需求或算法版本已改变，不能重放旧会话')
        try:
            session = cls(problem, SessionConfig(**report['config']))
            for record in report['history']:
                question = session.question()
                if question is None or any(record[key] != question[key] for key in question):
                    raise ValueError('回答记录与选题轨迹不一致')
                session.answer(record['id'], record['answer'])
            if session.report()['session_sha256'] != report.get('session_sha256'):
                raise ValueError('会话指纹不一致')
            return session
        except (KeyError, TypeError) as exc:
            raise ValueError('会话记录格式不合法') from exc
