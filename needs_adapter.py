"""队友 schema_version=1.0 契约到蓝色模块的接口；不调用模型或计算权重。"""
from copy import deepcopy
from needs_extraction import validate_result
from preferences import validate_needs
from schemas import CRITERIA

LEVEL_TO_BLUE = {
    '未提及': 'unknown', '不在意': 'unimportant', '一般': 'normal',
    '重要': 'important', '非常重要': 'very_important',
}


def to_blue_needs(result):
    errors = validate_result(result)
    if errors:
        raise ValueError('需求契约不合法：' + '；'.join(errors))
    if result['status'] == 'failed':
        raise ValueError('需求抽取失败，请重新输入或使用手动模式。')
    budget = result['budget']
    preferences = {item['criterion']: LEVEL_TO_BLUE[item['level']]
                   for item in result['preferences'] if item['criterion'] in CRITERIA}
    evidence = {item['criterion']: item['evidence'] for item in result['preferences']}
    evidence['budget'] = budget['evidence']
    out = {
        'hard_constraints': {
            'min_price': budget['min'], 'max_price': budget['max'],
            'min_storage_gb': result['min_storage'],
            'brands': list(result['brands']['include']),
            'excluded_brands': list(result['brands']['exclude']),
        },
        'target_screen_inches': result['target_screen'],
        'preferences': preferences,
        'unsupported_needs': [item['need'] for item in result['unsupported_needs']],
        'clarification_questions': [item['question'] for item in result['clarifications']],
        'evidence': evidence,
        'budget_reference': budget['reference'],
        'extraction_status': result['status'],
        'input_text': result['input_text'],
        'extraction_record': deepcopy(result),
    }
    return validate_needs(out)
