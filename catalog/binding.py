"""Content fingerprints bind a result to its actual input, not just version labels.

These hashes detect accidental mixing/mutation; they are not signed authenticity.
"""
from copy import deepcopy
import hashlib
import json
from .repository import CatalogRepository


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def decision_context(di, target_screen):
    return deepcopy({'snapshot_sha256': di.get('snapshot_sha256'),
        'selection_rules': di.get('selection_rules', {}),
        'criterion_definitions': di['criterion_definitions'], 'weights': di['weights'],
        'comparison_groups': di.get('comparison_groups', {}),
        'target_screen': target_screen,
        'target_transform': 'abs(screen_size_inch - target_screen) -> cost'})


def decision_fingerprint(di, target_screen):
    payload = {k: v for k, v in di.items() if k != 'candidates'}
    payload['candidates'] = CatalogRepository.as_records(di['candidates'])
    payload['effective_target_screen'] = target_screen
    return digest(payload)


def ranking_fingerprint(result):
    return digest({'status': result.status, 'reason': result.reason,
        'ranked': CatalogRepository.as_records(result.ranked),
        'backups': CatalogRepository.as_records(result.backups),
        'unscored_candidates': CatalogRepository.as_records(result.unscored_candidates),
        'dataset_version': result.dataset_version, 'feature_rule_version': result.feature_rule_version,
        'algorithm_version': result.algorithm_version, 'active_criteria': result.active_criteria,
        'decision_sha256': result.decision_sha256, 'decision_context': result.decision_context})


def verify_result(result):
    if not result.decision_sha256 or ranking_fingerprint(result) != result.ranking_sha256:
        raise ValueError('排名内容已变化，必须按同一决策输入重新计算')
