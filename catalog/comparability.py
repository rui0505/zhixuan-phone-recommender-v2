"""Conservative comparison groups and joint selection of gaming measurements.

## 为什么条件集和以前不一样

`conditions_json` 是从多个媒体评测页抓来的，键名由各来源的原文决定，历史上一直
不统一（`frame_mode` / `match_mode` / `ambient_temp` / `os` / `screen_brightness`）。
曾经要求 11 项条件**全部非空**才允许比较，结果是真实库里 147 条王者荣耀 run
**没有一条通过**，整个游戏维度静默失效——而测试用的是手工构造的"标准键"夹具，
所以一直是绿的。

现在的规则分两层：

- **必需**（决定负载是否可比）：机构 `institution`、画质 `quality`、帧目标 `fps_target`。
  同机构、同画质、同帧目标的帧率才放在一起比。
- **记录但不阻断**：环境温度、机身版本、测量方法等。它们**不参与分组**，
  但会在 `uncontrolled` 里如实交出去，由证据层说明"这些条件没有统一控制"。
  不参与分组不等于假装它们一致。

`normalise_game_conditions()` 负责把 legacy 键名收敛到规范名，并从 `frame_mode`
的原文里还原 `quality` 与 `fps_target`（"极致120帧" → quality=极致, fps_target=120）。
"""
import hashlib
import json
import math
import re
from collections import defaultdict
from numbers import Real
from .repository import known_date_key

APPROVED_MAPPINGS = {}  # None reviewed yet. A UI override cannot invent approval.

# 决定负载是否可比的条件。缺失则不可比。
GAME_CONDITIONS_REQUIRED = ('quality', 'fps_target')
# 记录但不参与分组；缺失会进 uncontrolled 由证据层披露。
GAME_CONDITIONS_RECORDED = ('game_version', 'render_resolution', 'scene', 'duration_min',
                            'ambient_temp_c', 'performance_mode', 'cooler',
                            'measurement_method', 'os_version', 'screen_brightness')

# 旧版常量保留，供外部引用；语义已由上面两个元组替代。
GAME_CONDITIONS = GAME_CONDITIONS_REQUIRED + GAME_CONDITIONS_RECORDED

# legacy 键名 -> 规范键名
_ALIASES = {'match_mode': 'scene', 'ambient_temp': 'ambient_temp_c', 'os': 'os_version'}
# frame_mode 原文里可能出现的画质词，长词优先，避免"极致画质"被"极致"抢先截断
_QUALITY_WORDS = ('最高可用画质', '最高画质', '极致画质', '画质拉满', '超高分辨率',
                  '极致', '极高', '高清', '顶级', '均衡', '流畅')
_MISSING_WORDS = ('unknown', 'not_comparable', '未公开', '未知', '未查到')


def _blank(value):
    return value is None or (isinstance(value, str) and
                             (not value.strip() or value.strip().lower() in _MISSING_WORDS))


def _quality_from_text(text):
    for word in _QUALITY_WORDS:
        if word in text:
            return word
    return None


def _fps_from_text(text):
    match = re.search(r'(\d+)\s*帧', text or '')
    return int(match.group(1)) if match else None


def normalise_game_conditions(conditions):
    """把各来源的不统一键名收敛到规范键。

    返回 `(values, extra)`：`values` 是规范键到取值的映射（空值已剔除），
    `extra` 保存原文里无法结构化、但可以披露的字段（`frame_mode`、`other`）。
    纯函数，不修改入参。
    """
    if not isinstance(conditions, dict):
        return {}, {}
    values, extra = {}, {}
    for key, value in conditions.items():
        if _blank(value):
            continue
        canonical = _ALIASES.get(key, key)
        if canonical in ('frame_mode', 'other', 'missing_reason'):
            extra[key] = value
            continue
        values.setdefault(canonical, value)

    frame_mode = conditions.get('frame_mode')
    if isinstance(frame_mode, str) and not _blank(frame_mode):
        if 'quality' not in values:
            quality = _quality_from_text(frame_mode)
            if quality:
                values['quality'] = quality
        if 'fps_target' not in values:
            target = _fps_from_text(frame_mode)
            if target:
                values['fps_target'] = target

    # 管线把无法归类的片段塞进 other，这里捞回可用的场景与时长。
    other = conditions.get('other')
    if isinstance(other, str) and not _blank(other):
        if 'scene' not in values:
            scene = re.search(r'\d+V\d+|单局|在线|训练', other)
            if scene:
                values['scene'] = scene.group(0)
        if 'duration_min' not in values:
            duration = re.search(r'(\d+)\s*分钟', other)
            if duration:
                values['duration_min'] = int(duration.group(1))
    return values, extra


def game_group(run):
    """返回 `(group_key, detail)` 表示可比；`(None, reason)` 表示不可比。

    成功时 `detail` 是 dict：机构、画质、帧目标、可读 label、已记录条件、
    以及 **未统一控制的条件清单**。失败时第二个元素是可读原因字符串。
    """
    raw = run.get('conditions_json') or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None, 'invalid_conditions_json'
    if not isinstance(raw, dict):
        return None, 'invalid_conditions_json'
    if raw.get('missing_reason'):
        return None, 'source_marked_missing:' + str(raw['missing_reason'])

    values, extra = normalise_game_conditions(raw)
    missing = []
    institution = run.get('institution')
    institution = institution.strip() if isinstance(institution, str) else ''
    if _blank(institution):
        missing.append('institution')
    for key in GAME_CONDITIONS_REQUIRED:
        if key not in values:
            missing.append(key)
    if missing:
        return None, 'missing_conditions:' + ','.join(missing)

    target = values['fps_target']
    if isinstance(target, bool) or not isinstance(target, Real) or not math.isfinite(target) or target <= 0:
        return None, 'invalid_condition:fps_target'
    quality = str(values['quality']).strip()
    if not quality:
        return None, 'invalid_condition:quality'

    key = {'institution': institution, 'quality': quality, 'fps_target': float(target)}
    payload = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    detail = dict(key, label=f'{institution} · {quality} · {int(target)}帧',
                  recorded={k: values[k] for k in GAME_CONDITIONS_RECORDED if k in values},
                  uncontrolled=[k for k in GAME_CONDITIONS_RECORDED if k not in values])
    if extra.get('frame_mode'):
        detail['frame_mode_raw'] = extra['frame_mode']
    return 'game::' + hashlib.sha256(payload.encode()).hexdigest(), detail


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
    groups, reasons, details = {}, {}, {}
    for run in runs:
        group, detail = game_group(run)
        if not group:
            reasons[run['run_id']] = detail
            continue
        if any(len(set(by_run[run['run_id']][k])) != 1 for k in active_metrics):
            reasons[run['run_id']] = 'missing_or_conflicting_metrics_in_same_run'
            continue
        groups[run['run_id']] = group
        details[run['run_id']] = detail
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
    selected_detail = None
    if selected_group:
        members = [details[r['run_id']] for r in runs if groups.get(r['run_id']) == selected_group]
        if members:
            selected_detail = dict(members[0])
            # 组内只要有一条 run 没记录某条件，就不能声称该条件已被统一控制。
            selected_detail['uncontrolled'] = [
                k for k in GAME_CONDITIONS_RECORDED
                if any(k in member['uncontrolled'] for member in members)]
            selected_detail['runs_in_group'] = len(members)
    return dict(comparison_group=selected_group, comparison_group_detail=selected_detail,
                run_by_sku=run_by_sku,
                run_by_model={k: sorted(v) for k, v in by_model.items()},
                coverage={k: len(v) for k, v in sorted(coverage.items())},
                rejection_reasons=reasons)
