"""Read-only performance capability reports using the actual comparability rules."""
from catalog.adapter import build_input

PERFORMANCE_METRICS = {
    'dxomark_photo': {'label': '拍照表现', 'unit': 'score',
                     'scope': '同一版 DXOMARK 照片子分，不代表视频或自拍表现'},
    'honor_of_kings_avg_fps': {'label': '王者荣耀平均帧率', 'unit': 'fps',
                             'scope': '仅限同协议、同条件的王者荣耀测试，不代表其他游戏'},
    'battery_mixed_usage_duration_min': {'label': '续航表现', 'unit': 'min',
                                       'scope': '同机构、同协议及混合负载条件的实测使用分钟数；容量不能代替续航'},
}
BATTERY_METRIC = 'battery_mixed_usage_duration_min'


def performance_capabilities(repo, needs, mode):
    capabilities = {}
    for key, info in PERFORMANCE_METRICS.items():
        if key == BATTERY_METRIC:
            capabilities[key] = {**info, 'available': False, 'status': 'unsupported_metric',
                'reason': '当前发布没有合格的混合使用续航实测指标，暂不能按续航排序；电池容量不代替续航。',
                'eligible_model_count': 0, 'eligible_sku_count': 0,
                'comparison_group': None, 'comparison_group_coverage': {}}
            continue
        di = build_input(repo, needs, {key: 1}, None, mode)
        criterion = di['criterion_definitions'][0]
        ready = di['decision_status'] == 'ready'
        capabilities[key] = {**info, 'available': ready,
            'status': 'available' if ready else di['decision_status'],
            'reason': '当前筛选下有同口径可比实测资料。' if ready else di['decision_reason'],
            'eligible_model_count': di['coverage_summary']['eligible_model_count'],
            'eligible_sku_count': di['coverage_summary']['eligible_sku_count'],
            'comparison_group': di['comparison_groups'].get(key),
            'comparison_group_coverage': criterion.get('comparison_group_coverage', {})}
    return capabilities
