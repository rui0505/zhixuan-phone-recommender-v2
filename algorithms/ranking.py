"""Frozen decision matrices, classic TODIM and tie-aware rank fusion.

TODIM uses pairwise candidate differences, not a claimed personal psychological
reference point. Its loss attenuation theta is an experimental parameter.
"""
from copy import deepcopy
import math
from numbers import Real

import numpy as np

from engine import rank_decision_input


def parameter(value, name, minimum=0, maximum=None, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, Real)
            or not math.isfinite(value) or value < minimum
            or (positive and value == minimum)
            or (maximum is not None and value > maximum)):
        raise ValueError(f'{name} 参数超出允许范围')
    return float(value)


def _weights(value, dimensions, *, strictly_positive=False):
    try:
        if any(isinstance(v, (bool, np.bool_)) for v in np.asarray(value, dtype=object).flat):
            raise ValueError('权重不能包含布尔值')
        raw = np.asarray(value)
        if raw.dtype.kind not in 'iuf':
            raise ValueError('权重必须是有限数值')
        w = np.asarray(raw, dtype=float)
    except (TypeError, OverflowError) as exc:
        raise ValueError('权重格式无效') from exc
    single = w.ndim == 1
    w = np.atleast_2d(w)
    if (w.ndim != 2 or w.shape[1] != dimensions or not len(w)
            or not np.isfinite(w).all() or (w < 0).any()
            or (strictly_positive and (w <= 0).any())):
        raise ValueError('权重维度或取值无效；TODIM 有效维度须严格为正')
    maximum = w.max(axis=1, keepdims=True)
    if (maximum <= 0).any():
        raise ValueError('至少启用一个指标')
    w = w / maximum
    w /= w.sum(axis=1, keepdims=True)
    if strictly_positive and (w <= 0).any():
        raise ValueError('权重比例过大，归一化后无法保持严格正数')
    return w, single


def midranks(scores):
    """Descending average ranks, treating differences <= rounding precision as ties."""
    values = np.asarray(scores, dtype=float)
    single = values.ndim == 1
    values = np.atleast_2d(values)
    if values.ndim != 2 or values.shape[1] == 0 or not np.isfinite(values).all():
        raise ValueError('排名分数须为非空有限数组')
    result = []
    for row in values:
        _, inverse, counts = np.unique(-np.round(row, 12), return_inverse=True,
                                       return_counts=True)
        ends = np.cumsum(counts)
        average = ends - (counts - 1) / 2
        result.append(average[inverse])
    out = np.asarray(result)
    return out[0] if single else out


def rrf(topsis_scores, todim_scores, *, alpha=.5, k=10):
    alpha = parameter(alpha, 'alpha', maximum=1)
    k = parameter(k, 'k', positive=True)
    left, right = np.asarray(topsis_scores), np.asarray(todim_scores)
    if left.shape != right.shape:
        raise ValueError('融合排序必须覆盖同一组候选')
    return alpha / (k + midranks(left)) + (1-alpha) / (k + midranks(right))


def _todim_components(utility):
    if (utility.ndim != 2 or not utility.size or not np.isfinite(utility).all()
            or (utility < 0).any() or (utility > 1).any()):
        raise ValueError('TODIM 需要 [0,1] 收益方向矩阵')
    # Precompute candidate differences once; particles only change the coefficients.
    gains, losses = np.zeros_like(utility), np.zeros_like(utility)
    for j in range(utility.shape[1]):
        delta = utility[:, None, j] - utility[None, :, j]
        gains[:, j] = np.sqrt(np.maximum(delta, 0)).sum(axis=1)
        losses[:, j] = np.sqrt(np.maximum(-delta, 0)).sum(axis=1)
    return gains, losses


def _todim_scores(gains, losses, weights, theta):
    raw = np.sqrt(weights) @ gains.T - (1 / (theta*np.sqrt(weights))) @ losses.T
    low = raw.min(axis=1, keepdims=True)
    span = raw.max(axis=1, keepdims=True) - low
    return np.divide(raw-low, span, out=np.full_like(raw, .5), where=span > 1e-14)


def todim(utility, weights, *, theta=1):
    theta = parameter(theta, 'theta', positive=True)
    utility = np.asarray(utility, dtype=float)
    gains, losses = _todim_components(utility)
    w, single = _weights(weights, utility.shape[1], strictly_positive=True)
    scores = _todim_scores(gains, losses, w, theta)
    return scores[0] if single else scores


class DecisionProblem:
    """A copied, canonical candidate universe validated by the original engine."""

    def __init__(self, di, target_screen=None):
        di = deepcopy(di)
        baseline = rank_decision_input(di, target_screen=target_screen)
        if baseline.status != 'ready':
            raise ValueError(baseline.reason)
        self.decision_sha256 = baseline.decision_sha256
        self.dataset_version = baseline.dataset_version
        self.feature_rule_version = baseline.feature_rule_version
        self.target_screen = target_screen
        indices = sorted(di['eligible_indices'],
                         key=lambda i: str(di['candidates'].iloc[i]['sku_id']))
        active = [j for j, d in enumerate(di['criterion_definitions'])
                  if di['weights'][d['metric_key']] > 0]
        self.keys = [di['criterion_definitions'][j]['metric_key'] for j in active]
        self.candidates = di['candidates'].iloc[indices].copy().reset_index(drop=True)
        self.ids = self.candidates['sku_id'].tolist()
        self.models = self.candidates['model_id'].tolist()
        self.raw = np.asarray([[di['X'][i][j] for j in active] for i in indices], dtype=float)
        self.directions = [di['criterion_definitions'][j]['direction'] for j in active]
        x = self.raw.copy()
        benefit = np.asarray([direction == 'benefit' for direction in self.directions])
        for j, direction in enumerate(self.directions):
            if direction == 'target':
                x[:, j] = np.abs(x[:, j] - target_screen)
        maximum = np.max(np.abs(x), axis=0)
        scaled = np.divide(x, maximum, out=np.zeros_like(x), where=maximum != 0)
        norms = np.linalg.norm(scaled, axis=0)
        normalized = np.divide(scaled, norms, out=np.zeros_like(x), where=norms != 0)
        best = np.where(benefit, normalized.max(axis=0), normalized.min(axis=0))
        worst = np.where(benefit, normalized.min(axis=0), normalized.max(axis=0))
        self._best_distance = (normalized-best)**2
        self._worst_distance = (normalized-worst)**2
        span = np.ptp(scaled, axis=0)
        utility = np.divide(scaled-scaled.min(axis=0), span,
                            out=np.zeros_like(scaled), where=span != 0)
        utility = np.where(benefit, utility, 1-utility)
        utility[:, span == 0] = 0  # constant dimensions contain no preference information
        self.utility = np.clip(utility, 0, 1)
        self._gains, self._losses = _todim_components(self.utility)
        self.initial_weights = _weights([di['weights'][k] for k in self.keys],
                                        len(self.keys))[0][0]
        self.baseline = baseline.ranked.set_index('sku_id').loc[self.ids, 'score'].to_numpy()
        self.provenance = deepcopy(di.get('provenance', []))
        self.selection_rules = deepcopy(di.get('selection_rules', {}))
        for array in (self.raw, self.utility, self.initial_weights, self.baseline,
                      self._best_distance, self._worst_distance, self._gains, self._losses):
            array.setflags(write=False)

    def scores(self, weights, *, theta=1, alpha=.5, k=10):
        theta = parameter(theta, 'theta', minimum=.05, maximum=20)
        w, single = _weights(weights, len(self.keys), strictly_positive=True)
        pos = np.sqrt(w**2 @ self._best_distance.T)
        neg = np.sqrt(w**2 @ self._worst_distance.T)
        topsis_scores = np.divide(neg, pos+neg, out=np.full_like(pos, .5), where=pos+neg > 0)
        todim_scores = _todim_scores(self._gains, self._losses, w, theta)
        fusion = rrf(topsis_scores, todim_scores, alpha=alpha, k=k)
        # VIKOR Q index only. The complete compromise-set acceptance rules are not applied.
        regret = (self.utility.max(axis=0)-self.utility)[None, :, :] * w[:, None, :]
        s, r = regret.sum(axis=2), regret.max(axis=2)
        def scale_rows(values):
            span = np.ptp(values, axis=1, keepdims=True)
            return np.divide(values-values.min(axis=1, keepdims=True), span,
                             out=np.zeros_like(values), where=span > 1e-14)
        q = .5*scale_rows(s) + .5*scale_rows(r)
        vikor = 1-q
        vikor[(np.ptp(s, axis=1) <= 1e-14) & (np.ptp(r, axis=1) <= 1e-14)] = .5
        result = dict(topsis=topsis_scores, todim=todim_scores, fusion=fusion,
                      vikor=vikor, vikor_q=1-vikor)
        return {key: value[0] for key, value in result.items()} if single else result
