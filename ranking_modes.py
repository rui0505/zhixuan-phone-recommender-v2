"""Production fixed-weight TOPSIS/TODIM RRF; no experimental session state.

Mathematics ported from algorithms/ranking.py in the reviewed experiment:
min-max benefit utilities, classic inverse-weight TODIM losses, average tied
ranks rounded to 12 decimals, and reciprocal-rank (not raw-score) fusion.
"""
from copy import deepcopy
import numpy as np

from catalog.binding import ranking_fingerprint
from engine import ALGORITHM_VERSION, rank_decision_input

ALGORITHM_VERSIONS = {'topsis': ALGORITHM_VERSION,
                      'hybrid_rrf': 'hybrid-rrf-1.0.0-experimental'}
SCORE_LABELS = {'topsis': 'TOPSIS 贴近度', 'hybrid_rrf': '融合排序分'}
HYBRID_PARAMETERS = {'theta': 1, 'alpha': .5, 'k': 10}


def _midranks(scores):
    _, inverse, counts = np.unique(-np.round(scores, 12), return_inverse=True,
                                   return_counts=True)
    return (np.cumsum(counts) - (counts - 1) / 2)[inverse]


def rank(di, target_screen=None, method='topsis'):
    if type(method) is not str or method not in ALGORITHM_VERSIONS:
        raise ValueError('未知排序方法')
    result = rank_decision_input(di, target_screen=target_screen)
    if method == 'topsis':
        return result  # Preserve the original result, context and hashes exactly.
    result.algorithm_version = ALGORITHM_VERSIONS[method]
    result.decision_context.update(method=method,
        algorithm_parameters=deepcopy(HYBRID_PARAMETERS))
    if result.status == 'ready':
        ranked = result.ranked.sort_values('input_index', kind='stable').copy()
        active = [j for j, criterion in enumerate(di['criterion_definitions'])
                  if di['weights'][criterion['metric_key']] > 0]
        x = np.asarray([[di['X'][i][j] for j in active]
                        for i in ranked['input_index']], dtype=float)
        directions = [di['criterion_definitions'][j]['direction'] for j in active]
        for j, direction in enumerate(directions):
            if direction == 'target':
                x[:, j] = np.abs(x[:, j] - target_screen)
        maximum = np.max(np.abs(x), axis=0)
        scaled = np.divide(x, maximum, out=np.zeros_like(x), where=maximum != 0)
        span = np.ptp(scaled, axis=0)
        utility = np.divide(scaled-scaled.min(axis=0), span,
                            out=np.zeros_like(scaled), where=span != 0)
        benefit = np.asarray([d == 'benefit' for d in directions])
        utility = np.where(benefit, utility, 1-utility)
        utility[:, span == 0] = 0
        utility = np.clip(utility, 0, 1)
        w = np.asarray([di['weights'][key] for key in result.active_criteria], dtype=float)
        w /= w.max(); w /= w.sum()
        if (w <= 0).any():
            raise ValueError('权重比例过大，无法保持有效维度严格正权重')
        gains, losses = np.zeros_like(utility), np.zeros_like(utility)
        for j in range(len(active)):
            delta = utility[:, None, j] - utility[None, :, j]
            gains[:, j] = np.sqrt(np.maximum(delta, 0)).sum(axis=1)
            losses[:, j] = np.sqrt(np.maximum(-delta, 0)).sum(axis=1)
        raw = np.sqrt(w) @ gains.T - (1 / np.sqrt(w)) @ losses.T
        spread = np.ptp(raw)
        todim = (raw-raw.min()) / spread if spread > 1e-14 else np.full(len(raw), .5)
        topsis = ranked['score'].to_numpy()
        left, right = _midranks(topsis), _midranks(todim)
        fusion = .5 / (10 + left) + .5 / (10 + right)
        ranked['topsis_score'], ranked['todim_score'] = topsis, todim
        ranked['topsis_rank'], ranked['todim_rank'] = left, right
        ranked['score'] = fusion
        # Only ID decides precision-level ties; it is never a performance claim.
        ranked['_tie_score'] = np.round(fusion, 12)
        result.ranked = ranked.sort_values(['_tie_score', 'sku_id'], ascending=[False, True],
                                           kind='stable').drop(columns='_tie_score')
        tied = np.unique(np.round(fusion, 12)).size < len(fusion)
        result.reason = ('混合排序（实验）：固定已确认权重的 TOPSIS + 经典 TODIM + RRF；'
                         '融合排序分仅用于当前可比候选集，尚未证明更准确。' +
                         ('存在并列；ID 次序不是优劣。' if tied else ''))
    result.ranking_sha256 = ranking_fingerprint(result)
    return result
