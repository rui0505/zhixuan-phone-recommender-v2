"""Deterministic consumer wording derived only from a verified evidence bundle."""
import json
import math
import re
from datetime import date, datetime
from numbers import Real

from catalog.evidence import verify_bundle
from catalog.read_views import safe_url


_LABELS = {'price_cny': '价格', 'weight_g': '重量', 'storage_gb': '存储',
           'refresh_rate_hz': '屏幕刷新率', 'screen_size_inch': '屏幕尺寸',
           'battery_capacity_mah': '电池容量', 'dxomark_photo': '照片子分',
           'honor_of_kings_avg_fps': '王者荣耀测试'}
_PHOTO_RULES = {'dxomark_v4': '第4版', 'dxomark_v5': '第5版', 'dxomark_v6': '第6版'}
_MEASURED = {'dxomark_photo', 'honor_of_kings_avg_fps'}


def _number(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _fmt(value):
    # Screen deviations need decimal rounding, not binary floating-point tails.
    return f'{value:.4f}'.rstrip('0').rstrip('.') if value != int(value) else str(int(value))


def _record_date(value):
    if isinstance(value, str):
        try:
            if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
                return date.fromisoformat(value).isoformat()
            # Validate the entire timestamp. fromisoformat alone permits arbitrary
            # single-character separators, and slicing would turn ranges into dates.
            if re.fullmatch(
                    r'[0-9]{4}-[0-9]{2}-[0-9]{2}[T ](?:[01][0-9]|2[0-3]):[0-5][0-9]'
                    r'(?::[0-5][0-9](?:\.[0-9]{1,6})?)?'
                    r'(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?', value):
                return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            pass
    return None


def _conditions(fact):
    value = fact.get('conditions_json')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _has_source(fact):
    return any(isinstance(source, dict) and safe_url(source.get('url'))
               for source in fact.get('sources', []))


def _usable(fact):
    key = fact.get('metric_key')
    value = fact.get('value')
    if key not in _LABELS or not _number(value) or value <= 0 or fact.get('status') == 'missing':
        return False
    if key not in _MEASURED:
        return True
    if (fact.get('status') != 'measured' or not _has_source(fact)
            or not fact.get('protocol') or not fact.get('comparison_group')):
        return False
    if key == 'dxomark_photo':
        return fact['comparison_group'] in _PHOTO_RULES
    conditions = _conditions(fact)
    return (str(fact['comparison_group']).startswith('game::') and bool(conditions)
            and all(conditions.get(key) is not None for key in
                    ('quality', 'fps_target', 'duration_min', 'game_version')))


def _price_kind(model):
    condition = {'new': '新机', 'used': '二手'}.get(model.get('condition'), '成色未注明的')
    return condition + ('估计价' if model.get('is_estimated') else '记录价')


def _price_match(fact, model, needs):
    value = fact['value']
    observed = _record_date(model.get('observed_at') or fact.get('observed_at'))
    when = f'{observed} 的历史快照' if observed else '记录日期不明确'
    text = f'{_price_kind(model)}为 {_fmt(value)} 元（{when}）。'
    budget = needs.get('budget') or {}
    low, high = budget.get('min'), budget.get('max')
    if (_number(low) or _number(high)) and (not _number(low) or value >= low) and (
            not _number(high) or value <= high):
        if _number(low) and _number(high):
            text += f'落在你确认的 {_fmt(low)}—{_fmt(high)} 元预算范围内。'
        elif _number(high):
            text += f'未超过你确认的 {_fmt(high)} 元预算上限。'
        else:
            text += f'符合你确认的至少 {_fmt(low)} 元预算条件。'
    return text


def _match(fact, model, needs, target):
    key, value = fact['metric_key'], _fmt(fact['value'])
    if key == 'price_cny':
        return _price_match(fact, model, needs)
    if key == 'weight_g':
        return f'机身重量记录为 {value} 克，可作为日常携带的参考。'
    if key == 'storage_gb':
        text = f'存储规格为 {value} GB。'
        minimum = needs.get('min_storage')
        if _number(minimum) and fact['value'] >= minimum:
            text += f'达到你确认的至少 {_fmt(minimum)} GB 要求。'
        return text
    if key == 'refresh_rate_hz':
        return f'屏幕刷新率记录为 {value} Hz；这是屏幕参数，不代表游戏帧率。'
    if key == 'screen_size_inch':
        if not _number(target) or target <= 0:
            return None
        return (f'屏幕为 {value} 英寸，与你确认的 {_fmt(target)} 英寸目标'
                f'相差 {_fmt(abs(fact["value"] - target))} 英寸。')
    if key == 'battery_capacity_mah':
        return f'电池标称容量为 {value} mAh，供你参考容量大小。'
    if key == 'dxomark_photo':
        return (f'DXOMARK {_PHOTO_RULES[fact["comparison_group"]]}规则下，'
                f'照片子分为 {value} 分（仅指照片这一项）。')
    if key == 'honor_of_kings_avg_fps':
        conditions = _conditions(fact)
        details = []
        quality = conditions.get('quality')
        if isinstance(quality, str) and re.fullmatch(r'[A-Za-z0-9\u4e00-\u9fff +.%-]{1,24}', quality):
            details.append(quality + '画质')
        for field, unit in (('fps_target', '帧目标'), ('duration_min', '分钟'), ('ambient_temp_c', '℃环境')):
            if _number(conditions.get(field)):
                details.append(_fmt(conditions[field]) + unit)
        detail = '；' + '、'.join(details) if details else ''
        return f'记录的《王者荣耀》测试平均帧率为 {value} fps（仅限该次测试规则和条件{detail}）。'
    return None


def _comparable(first, other, models):
    if (first.get('metric_key') != other.get('metric_key')
            or first.get('model_id') == other.get('model_id')
            or not first.get('comparison_group')
            or first['comparison_group'] != other.get('comparison_group') or not _usable(other)):
        return False
    key = first['metric_key']
    if key in _MEASURED and first.get('protocol') != other.get('protocol'):
        return False
    if key == 'honor_of_kings_avg_fps' and _conditions(first) != _conditions(other):
        return False
    if key == 'price_cny':
        a, b = models[first['sku_id']], models.get(other['sku_id'], {})
        # A cheaper used or older snapshot is not presented as a like-for-like bargain.
        observed_a, observed_b = _record_date(a.get('observed_at')), _record_date(b.get('observed_at'))
        return (observed_a is not None and observed_a == observed_b
                and a.get('condition') in ('new', 'used') and a['condition'] == b.get('condition')
                and a.get('is_estimated') == b.get('is_estimated'))
    return True


def _cost(fact, target):
    key, value = fact['metric_key'], fact['value']
    if key == 'screen_size_inch':
        return abs(value - target) if _number(target) and target > 0 else None
    return value if key in ('price_cny', 'weight_g') else -value


def _tradeoff(fact, other, target):
    key, value, peer = fact['metric_key'], _fmt(fact['value']), _fmt(other['value'])
    if key == 'screen_size_inch':
        return (f'同次候选中有屏幕距离目标仅 {_fmt(abs(other["value"] - target))} 英寸的机型；'
                f'本机相差 {_fmt(abs(fact["value"] - target))} 英寸，尺寸更偏离你的目标。')
    if key == 'price_cny':
        return f'同日期、同成色的候选记录中有 {peer} 元的机型；本机记录价 {value} 元，需要多留一些预算。'
    if key == 'weight_g':
        return f'同次候选中有重量 {peer} 克的机型；本机记录为 {value} 克，携带时需要接受更高的重量。'
    if key == 'dxomark_photo':
        return f'同一版 DXOMARK 照片规则下，候选中有 {peer} 分的机型；本机照片子分为 {value} 分。'
    if key == 'honor_of_kings_avg_fps':
        return f'同一规则和条件的《王者荣耀》测试中，候选中有平均 {peer} fps 的机型；本机记录为 {value} fps。'
    unit = {'storage_gb': 'GB', 'refresh_rate_hz': 'Hz', 'battery_capacity_mah': 'mAh'}[key]
    return f'同次候选中有{_LABELS[key]}为 {peer} {unit} 的机型；本机记录为 {value} {unit}。'


def recommendation_reason(bundle, sku_id) -> dict:
    """Explain one SKU without changing evidence, ranking, preferences, or inputs.

    ``fact_ids`` includes both sides of any comparison. Wording is intentionally
    separate from the original technical explanation and never invokes an LLM.
    """
    verify_bundle(bundle)
    models = {model['sku_id']: model for model in bundle['models']}
    if sku_id not in models:
        raise ValueError('该配置不在本次推荐证据中')
    model = models[sku_id]
    facts = bundle['facts']
    own = [(ident, fact) for ident, fact in facts.items() if fact.get('sku_id') == sku_id]
    context = bundle.get('decision_context') or {}
    weights = context.get('weights') or {}
    target = context.get('target_screen')
    needs = bundle.get('needs') or {}
    active = [(ident, fact) for ident, fact in own
              if _number(weights.get(fact.get('metric_key'))) and weights[fact['metric_key']] > 0]
    active.sort(key=lambda item: -weights[item[1]['metric_key']])
    matches, matched_keys, tradeoffs, limitations, used_ids = [], [], [], [], []

    def cite(*idents):
        for ident in idents:
            if ident not in used_ids:
                used_ids.append(ident)

    def limit(text, ident=None):
        if text not in limitations:
            limitations.append(text)
        if ident is not None:
            cite(ident)

    usable = []
    for ident, fact in active:
        key = fact.get('metric_key')
        if not _usable(fact):
            if key in _MEASURED and _number(fact.get('value')) and not _has_source(fact):
                limit(f'{_LABELS[key]}缺少可回查的来源，暂不把它作为匹配理由。', ident)
            elif key in _LABELS:
                limit(f'{_LABELS[key]}资料缺失或暂时不能直接对照，需要补充核实。', ident)
            continue
        sentence = _match(fact, model, needs, target)
        if sentence is None:
            if key == 'screen_size_inch':
                limit('尚未确认目标屏幕尺寸，暂时不能判断尺寸是否合适。', ident)
            continue
        usable.append((ident, fact))
        if len(matches) < 3:
            matches.append(sentence)
            matched_keys.append(key)
            cite(ident)
        if key == 'honor_of_kings_avg_fps':
            scope = '这是机型级测试，不能当作每个配置都单独测过。' if fact.get('applicable_scope') == 'model' else '测试只覆盖资料中注明的配置范围。'
            limit(scope + '结果只对应记录中的《王者荣耀》规则和环境，不代表所有游戏表现。', ident)
        elif key == 'dxomark_photo':
            scope = '这是机型级照片测试，不能当作每个配置都单独测过。' if fact.get('applicable_scope') == 'model' else ''
            limit(scope + '照片子分只覆盖对应版本的测试规则，不能直接说明所有场景下的成片效果。', ident)
        elif key == 'battery_capacity_mah':
            limit('标称电池容量不能直接说明一次充电能用多久。', ident)
        elif fact.get('status') == 'spec_reference' and key in ('weight_g', 'screen_size_inch'):
            limit(f'{_LABELS[key]}来自规格记录，实际握持感还需要试拿。', ident)
        if key not in _MEASURED and not _has_source(fact):
            limit('部分参数仅有资料记录，缺少可回查的来源，购买前建议再次核实。', ident)

    # At most one concrete compromise keeps the default card readable.
    for ident, fact in usable:
        peers = [(other_id, other) for other_id, other in facts.items()
                 if other.get('sku_id') in models and _comparable(fact, other, models)]
        if not peers:
            continue
        own_cost = _cost(fact, target)
        if own_cost is None:
            continue
        peer_id, peer = min(peers, key=lambda item: _cost(item[1], target))
        peer_cost = _cost(peer, target)
        if own_cost > peer_cost and not math.isclose(own_cost, peer_cost, abs_tol=1e-9):
            tradeoffs.append(_tradeoff(fact, peer, target))
            cite(ident, peer_id)
            break

    price = next(((ident, fact) for ident, fact in own
                  if fact.get('metric_key') == 'price_cny' and _number(fact.get('value'))), None)
    if price:
        ident, fact = price
        observed = _record_date(model.get('observed_at') or fact.get('observed_at'))
        when = f'{observed} 的历史记录' if observed else '日期不明确的历史记录'
        limit(f'价格来自 {when}；购买前请重新核实价格、成色和库存。', ident)
    if not matches:
        limit('目前没有足够的已核实资料对应这次设置的重点，暂不强行给出匹配结论。')
    if not limitations and not tradeoffs:
        limit('当前推荐仅依据已记录的参数，实际使用感受仍需进一步确认。')
    summary = ('结合这次确认的重点，可以先看这款配置的' + '、'.join(_LABELS[key] for key in matched_keys) + '。'
               if matches else '现有资料暂时不足以说明这款配置与你的重点如何匹配。')
    return {'summary': summary, 'matches': matches, 'tradeoffs': tradeoffs,
            'limitations': limitations, 'fact_ids': used_ids}
