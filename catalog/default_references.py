"""Preserve initial preferences without manufacturing missing measured scores."""
from .adapter import build_input, CONTROL_MAP

OPTIONAL_REFERENCES = ('dxomark_photo','honor_of_kings_avg_fps')
POLICY_VERSION = 'initial-measured-references-1'


def build_reference_input(repo, needs, weights, target_screen, mode,
                          defaulted_metrics=(), game_scope='honor_of_kings'):
    requested = {CONTROL_MAP.get(k,k):v for k,v in weights.items()}
    di = build_input(repo,needs,requested,target_screen,mode)
    optional = [k for k in OPTIONAL_REFERENCES if k in defaulted_metrics and requested.get(k,0)>0]
    definitions = di['criterion_definitions']
    required = [j for j,c in enumerate(definitions) if requested[c['metric_key']]>0 and c['metric_key'] not in optional]
    baseline = [i for i,row in enumerate(di['missing_mask']) if all(not row[j] for j in required)]
    effective = dict(requested)
    deferred = []
    for key in optional:
        column = next(j for j,c in enumerate(definitions) if c['metric_key']==key)
        covered = [i for i in baseline if not di['missing_mask'][i][column]]
        if key=='honor_of_kings_avg_fps' and game_scope!='honor_of_kings':
            reason = '游戏范围未确认或不支持；保留初始偏好，暂不计入综合分'
        elif not baseline or len(covered)!=len(baseline):
            reason = '可比实测未覆盖其他条件下的全部候选；保留初始偏好与已有资料，暂不计入综合分'
        else:
            continue
        effective[key] = 0
        deferred.append({'metric_key':key,'initial_weight':requested[key],
            'covered_sku_count':len(covered),'reference_sku_count':len(baseline),'reason':reason})
    if not any(effective.values()):
        raise ValueError('仅剩的初始参考项缺少可比资料，请启用一项有数据的偏好')
    if deferred:
        di = build_input(repo,needs,effective,target_screen,mode)
    if defaulted_metrics:
        di['selection_rules']['default_reference_policy'] = {
            'version':POLICY_VERSION,'defaulted_metrics':sorted(defaulted_metrics),
            'requested_weights':requested,'deferred':deferred}
    return di
