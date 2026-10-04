"""蓝色任务：校验需求、等级映射、归一化和固定规则修正。

不调用大模型。先用 example_needs.json 开发，再接需求抽取模块的字典。
"""
from copy import deepcopy
import math
from schemas import LEVEL_MAP, UNMENTIONED_LEVEL

KEYS = ('price', 'weight', 'storage', 'refresh', 'screen')
LABELS = {
    'price': '价格更低', 'weight': '机身更轻', 'storage': '存储更大',
    'refresh': '刷新率更高', 'screen': '屏幕接近目标尺寸',
}
LEVEL_SCORES = {
    'unknown': UNMENTIONED_LEVEL, 'unimportant': LEVEL_MAP['不在意'],
    'normal': LEVEL_MAP['一般'], 'important': LEVEL_MAP['重要'],
    'very_important': LEVEL_MAP['非常重要'],
}
LEVEL_LABELS = {
    'unknown': '未提及（系统默认1）', 'unimportant': '不在意',
    'normal': '一般', 'important': '重要', 'very_important': '非常重要',
}


def validate_needs(needs):
    """校验已解码的 JSON 字典，返回独立副本；拒绝含糊或异常输入。"""
    if not isinstance(needs, dict):
        raise ValueError('需求必须是JSON对象。')
    required = {'hard_constraints', 'target_screen_inches', 'preferences',
                'unsupported_needs', 'clarification_questions', 'evidence'}
    if required - needs.keys():
        raise ValueError('需求缺少字段：' + '、'.join(sorted(required - needs.keys())))
    out = deepcopy(needs)
    hard = out['hard_constraints']
    required_hard = {'max_price', 'min_storage_gb', 'brands'}
    allowed_hard = required_hard | {'min_price', 'excluded_brands'}
    if (not isinstance(hard, dict) or not required_hard <= set(hard)
            or not set(hard) <= allowed_hard):
        raise ValueError('hard_constraints必须包含max_price、min_storage_gb、brands。')
    for key in ('min_price', 'max_price', 'min_storage_gb'):
        val = hard.get(key)
        if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float))
                                or not math.isfinite(val) or val < 0
                                or (key == 'min_storage_gb' and val == 0)):
            raise ValueError(key + '必须为正的有限数值或null。')
    if not isinstance(hard['brands'], list) or not all(
            isinstance(v, str) and v.strip() for v in hard['brands']):
        raise ValueError('brands必须是品牌名称列表。')
    excluded = hard.get('excluded_brands', [])
    if not isinstance(excluded, list) or not all(isinstance(v, str) and v.strip() for v in excluded):
        raise ValueError('excluded_brands必须是品牌名称列表。')
    if (hard.get('min_price') is not None and hard['max_price'] is not None
            and hard['min_price'] > hard['max_price']):
        raise ValueError('预算下限不能高于上限。')
    target = out['target_screen_inches']
    if target is not None and (isinstance(target, bool) or not isinstance(target, (int, float))
                               or not math.isfinite(target) or not 3 <= target <= 10):
        raise ValueError('目标屏幕尺寸必须为3—10英寸的数值或null；界面可再确认。')
    pref = out['preferences']
    if not isinstance(pref, dict) or set(pref) != set(KEYS):
        raise ValueError('preferences必须包含五项固定指标。')
    for key in KEYS:
        if not isinstance(pref[key], str) or pref[key] not in LEVEL_SCORES:
            raise ValueError(key + '的偏好等级不合法。')
    for key in ('unsupported_needs', 'clarification_questions'):
        if not isinstance(out[key], list) or not all(isinstance(v, str) for v in out[key]):
            raise ValueError(key + '必须是文本列表。')
    if not isinstance(out['evidence'], dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in out['evidence'].items()):
        raise ValueError('evidence必须是字段到原话的文本字典。')
    return out


def normalize_weights(scores):
    """原始分值与最终权重使用同一归一化函数；不修改输入。"""
    if not isinstance(scores, dict) or set(scores) != set(KEYS):
        raise ValueError('权重必须包含五项固定指标。')
    clean = {}
    for key in KEYS:
        value = scores[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('权重必须是数值。')
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError('权重必须非负且有限。')
        clean[key] = value
    total = math.fsum(clean.values())
    if not math.isfinite(total) or total <= 0:
        raise ValueError('请至少启用一项偏好；全零权重不能评分。')
    return {key: value / total for key, value in clean.items()}


def build_initial_weights(needs):
    """只接受确认后的需求；返回权重、分值和来源记录。

    目标屏幕为空时关闭屏幕评分。调用方应明确让用户确认此行为。
    """
    needs = validate_needs(needs)
    raw = {key: LEVEL_SCORES[needs['preferences'][key]] for key in KEYS}
    effective = dict(raw)
    inactive = []
    if needs['target_screen_inches'] is None:
        effective['screen'] = 0
        inactive.append('screen')
    return {
        'weights': normalize_weights(effective),
        'raw_scores': raw, 'effective_scores': effective,
        'sources': {key: ('system_default' if needs['preferences'][key] == 'unknown'
                          else 'confirmed_level') for key in KEYS},
        'inactive_keys': inactive,
    }


def filter_candidates(frame, needs):
    """按确认后的硬条件筛选，不额外加入1000元下限或256GB默认值。"""
    hard = validate_needs(needs)['hard_constraints']
    out = frame.copy()
    if hard.get('min_price') is not None:
        out = out[out.price >= hard['min_price']]
    if hard['max_price'] is not None:
        out = out[out.price <= hard['max_price']]
    if hard['min_storage_gb'] is not None:
        out = out[out.storage >= hard['min_storage_gb']]
    if hard['brands']:
        out = out[out.brand.isin(hard['brands'])]
    if hard.get('excluded_brands'):
        out = out[~out.brand.isin(hard['excluded_brands'])]
    return out


def update_weights(weights, preferred_key, other_key, step=0.25):
    """偏好项乘1+step，另一项乘1-step，再归一化；仅是规则修正。"""
    clean = normalize_weights(weights)
    if preferred_key not in KEYS or other_key not in KEYS or preferred_key == other_key:
        raise ValueError('比较必须涉及两个不同的有效指标。')
    if isinstance(step, bool) or not isinstance(step, (int, float)) or not 0 < step < 1:
        raise ValueError('修正幅度必须在0与1之间。')
    if clean[preferred_key] == 0 or clean[other_key] == 0:
        raise ValueError('零权重指标不参加取舍追问；请先修改偏好。')
    clean[preferred_key] *= 1 + step
    clean[other_key] *= 1 - step
    return normalize_weights(clean)


def apply_answer(weights, question, answer, answered_ids):
    """页面层的幂等更新：同题不重复应用；跳过与无倾向都保留原权重。"""
    if answer not in {'a', 'b', 'indifferent', 'skip'}:
        raise ValueError('回答必须是a、b、indifferent或skip。')
    if question['id'] in answered_ids:
        raise ValueError('这道题已经提交，不能重复更新。')
    before = normalize_weights(weights)
    after = dict(before)
    if answer in {'a', 'b'}:
        first, second = question['a_key'], question['b_key']
        if answer == 'b':
            first, second = second, first
        after = update_weights(before, first, second)
    return {
        'weights': after,
        'answered_ids': [*answered_ids, question['id']],
        'record': {'question_id': question['id'], 'answer': answer,
                   'before': before, 'after': after},
    }


QUESTION_BANK = (
    {'id': 'light_price', 'a_key': 'weight', 'b_key': 'price',
     'text': '假设其他条件相同，你更愿意选择哪种改善？',
     'a_label': '机身轻20克', 'b_label': '便宜200元'},
    {'id': 'storage_price', 'a_key': 'storage', 'b_key': 'price',
     'text': '假设其他条件相同，你更愿意选择哪种改善？',
     'a_label': '存储从256GB增加到512GB', 'b_label': '便宜300元'},
    {'id': 'refresh_price', 'a_key': 'refresh', 'b_key': 'price',
     'text': '假设其他条件相同，你更愿意选择哪种改善？',
     'a_label': '刷新率从60Hz提高到120Hz', 'b_label': '便宜200元'},
)


def next_question(weights, answered_ids, preferences, max_questions=2):
    """最多两题；优先明确重视的指标，未知等级也可参与待确认取舍。"""
    clean = normalize_weights(weights)
    if len(answered_ids) >= max_questions:
        return None
    eligible = [dict(q) for q in QUESTION_BANK
                if q['id'] not in answered_ids
                and clean[q['a_key']] > 0 and clean[q['b_key']] > 0]
    eligible.sort(key=lambda q: LEVEL_SCORES[preferences[q['a_key']]], reverse=True)
    return eligible[0] if eligible else None
