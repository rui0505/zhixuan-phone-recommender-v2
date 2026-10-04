"""Conservative comparison groups and joint selection of gaming measurements."""
import hashlib
import json
import math
from collections import defaultdict
from numbers import Real
from .repository import known_date_key

APPROVED_MAPPINGS = {}  # None reviewed yet. A UI override cannot invent approval.
GAME_CONDITIONS = ('game_version', 'quality', 'render_resolution', 'fps_target', 'scene',
                   'duration_min', 'ambient_temp_c', 'performance_mode', 'cooler',
                   'measurement_method', 'os_version')


def game_group(run):
    conditions = run.get('conditions_json') or {}
    if isinstance(conditions, str):
        try:
            conditions = json.loads(conditions)
        except (ValueError, TypeError):
            return None, 'invalid_conditions_json'
    if not isinstance(conditions, dict):
        return None, 'invalid_conditions_json'
    values = {k: run.get(k) for k in ('institution', 'benchmark', 'protocol', 'applicable_scope')}
    values.update({k: conditions.get(k) for k in GAME_CONDITIONS})
    missing = [k for k, v in values.items() if v is None or v == '' or
               (isinstance(v, str) and v.strip().lower() in ('unknown','未公开','未知'))]
    if missing:
        return None, 'missing_conditions:' + ','.join(missing)
    for key in ('fps_target', 'duration_min', 'ambient_temp_c'):
        val = values[key]
        if isinstance(val, bool) or not isinstance(val, Real) or not math.isfinite(val):
            return None, 'invalid_condition:' + key
        if key != 'ambient_temp_c' and val <= 0:
            return None, 'invalid_condition:' + key
        values[key] = float(val)
    for key in set(values).difference(('fps_target','duration_min','ambient_temp_c')):
        if not isinstance(values[key], str) or not values[key].strip():
            return None, 'invalid_condition:' + key
        values[key] = values[key].strip()
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',',':'))
    return 'game::' + hashlib.sha256(payload.encode()).hexdigest(), None


def applicable_run(run, candidate):
    if run.get('model_id') != candidate['model_id']:
        return False
    if run.get('sku_id'):
        return run['sku_id'] == candidate['sku_id']
    if run.get('variant_id'):
        return run['variant_id'] == candidate['variant_id'] and not candidate.get('_variant_hardware_conflict')
    return run.get('applicable_scope') == 'model' and not candidate.get('_model_hardware_conflict')


def select_game_runs(runs, observations, active_metrics, candidates, approved_mappings):
    """No best-value stitching. A chosen run must contain every active game metric."""
    by_run = defaultdict(lambda: defaultdict(list))
    for obs in observations:
        value = obs.get('value_num')
        if value is not None and isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value):
            by_run[obs['run_id']][obs['metric_key']].append(value)
    groups, reasons = {}, {}
    for run in runs:
        group, reason = game_group(run)
        if not group:
            reasons[run['run_id']] = reason
            continue
        if any(len(set(by_run[run['run_id']][k])) != 1 for k in active_metrics):
            reasons[run['run_id']] = 'missing_or_conflicting_metrics_in_same_run'
            continue
        groups[run['run_id']] = group
    coverage = defaultdict(set)
    usable = defaultdict(list)
    for candidate in candidates:
        for run in runs:
            group = groups.get(run['run_id'])
            if group and applicable_run(run, candidate):
                usable[candidate['sku_id']].append(run)
                coverage[group].add(candidate['model_id'])
    ordered = sorted(coverage, key=lambda g: (-len(coverage[g]), g))
    selected_group = ordered[0] if ordered else None
    run_by_sku = {}
    for candidate in candidates:
        eligible = [r for r in usable[candidate['sku_id']] if groups[r['run_id']] == selected_group]
        eligible.sort(key=lambda r: r['run_id'])
        eligible.sort(key=lambda r: known_date_key(r.get('run_date')), reverse=True)
        run_by_sku[candidate['sku_id']] = eligible[0]['run_id'] if eligible else None
    by_model = defaultdict(set)
    for c in candidates:
        if run_by_sku[c['sku_id']]:
            by_model[c['model_id']].add(run_by_sku[c['sku_id']])
    return dict(comparison_group=selected_group, run_by_sku=run_by_sku,
                run_by_model={k: sorted(v) for k,v in by_model.items()},
                coverage={k:len(v) for k,v in sorted(coverage.items())}, rejection_reasons=reasons)
