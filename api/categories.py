"""Read-only scenario reference pools; evidence coverage is not suitability certification."""
from math import isfinite
from numbers import Real

from catalog.read_views import safe_urls


CATEGORY_RULE_VERSION = 'scenario-categories-v1'
CATEGORY_LABELS = {'gaming': '游戏手机', 'senior': '长辈友好', 'photography': '拍照手机'}
_METRIC_CATEGORY = {
    'honor_of_kings_avg_fps': 'gaming',
    'dxomark_photo': 'photography',
    'phonearena_camera_photo': 'photography',
}
_PENDING_REASONS = {
    'gaming': '尚缺少有可回查来源的有效平均帧率资料，待评估；芯片、屏幕刷新率和跑分不能替代游戏实测。',
    'senior': '尚缺少已核实的适老功能与易用性资料，待评估；不能仅凭价格或电池容量判断。',
    'photography': '尚缺少有可回查来源的有效照片子项，待评估；相机总分和硬件规格不能替代照片评测。',
}
_AVAILABLE_REASONS = {
    'gaming': '有平均帧率评测资料与可回查来源，可作为游戏选机参考；有资料不代表性能优秀。资料为机型级，各配置未独立实测，不同测试条件不混合比较。',
    'photography': '有照片子项评测资料与可回查来源，可作为拍照选机参考；有资料不代表拍照优秀。资料为机型级，各配置未独立实测，不同机构或协议不混合比较。',
}


def model_categories(repo) -> dict[str, list[dict]]:
    """Derive all model categories from positive observations with safe provenance.

    Only observations explicitly applying to the complete model may cover a model
    category. Keep each run's numbers and protocol separate; this view neither
    computes a performance score nor changes the repository or ranking inputs.
    """
    result = {
        model['model_id']: [
            {'id': category_id, 'label': label, 'status': 'pending_review',
             'reason': _PENDING_REASONS[category_id], 'evidence_refs': [],
             'rule_version': CATEGORY_RULE_VERSION}
            for category_id, label in CATEGORY_LABELS.items()
        ]
        for model in repo.as_records(repo.list_models())
    }
    by_category = {model_id: {category['id']: category for category in categories}
                   for model_id, categories in result.items()}
    rows = repo.as_records(repo._q(
        "SELECT r.model_id,r.run_id,r.source_id,r.institution,r.protocol,"
        "r.conditions_json,r.applicable_scope,r.comparison_group,"
        "o.observation_id,o.metric_key,o.value_num,o.unit,o.missing_reason,"
        "s.url,s.publisher FROM benchmark_runs r "
        "JOIN metric_observations o ON o.run_id=r.run_id "
        "JOIN sources s ON s.source_id=r.source_id "
        "WHERE r.subject_type='model' AND r.subject_id=r.model_id "
        "AND r.applicable_scope='model' AND r.variant_id IS NULL AND r.sku_id IS NULL "
        "AND o.metric_key IN (?,?,?) ORDER BY r.model_id,r.run_id,o.observation_id",
        tuple(_METRIC_CATEGORY)))
    for row in rows:
        value = row['value_num']
        urls = safe_urls(row['url'])
        if (row['model_id'] not in by_category or not urls or row['missing_reason']
                or isinstance(value, bool) or not isinstance(value, Real)
                or not isfinite(value) or value <= 0):
            continue
        category_id = _METRIC_CATEGORY[row['metric_key']]
        category = by_category[row['model_id']][category_id]
        category['status'] = 'evidence_available'
        category['reason'] = _AVAILABLE_REASONS[category_id]
        category['evidence_refs'].append({
            'run_id': row['run_id'], 'observation_id': row['observation_id'],
            'metric_key': row['metric_key'], 'value': value, 'unit': row['unit'],
            'source_id': row['source_id'], 'publisher': row['publisher'],
            'institution': row['institution'], 'urls': urls, 'protocol': row['protocol'],
            'conditions_json': row['conditions_json'],
            'applicable_scope': row['applicable_scope'],
            'comparison_group': row['comparison_group'],
        })
    return result
