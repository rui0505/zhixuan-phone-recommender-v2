"""Version-bound evidence, deterministic language, validated fact selection only."""
import hashlib
import json
from .repository import CatalogRepository
from .binding import decision_fingerprint, verify_result

LABELS={'price_cny':'记录价格','weight_g':'重量','storage_gb':'存储','refresh_rate_hz':'屏幕刷新率',
        'screen_size_inch':'屏幕尺寸','battery_capacity_mah':'电池标称容量',
        'chipset_tier':'处理器档位',
        'dxomark_photo':'DXOMARK 照片子分','honor_of_kings_avg_fps':'王者荣耀平均帧率'}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def verify_bundle(bundle):
    if digest({k:v for k,v in bundle.items() if k!='evidence_sha256'})!=bundle.get('evidence_sha256'):
        raise ValueError('证据内容已变化，必须按同一数据版本重新生成')


def build_evidence(di,result,needs: dict, *, sku_ids=None) -> dict:
    verify_result(result)
    if decision_fingerprint(di,result.decision_context['target_screen'])!=result.decision_sha256:
        raise ValueError('排名与当前决策输入内容不一致')
    if (result.dataset_version!=di['dataset_version'] or result.feature_rule_version!=di['feature_rule_version']):
        raise ValueError('排名和证据数据版本不一致')
    candidates=CatalogRepository.as_records(di['candidates']);models=[];facts={}
    ranked=CatalogRepository.as_records(result.ranked)
    if sku_ids is not None:
        if len(set(sku_ids))!=len(sku_ids) or not set(sku_ids)<=set(r['sku_id'] for r in ranked):
            raise ValueError('展示 SKU 必须是本次排名的无重复子集')
        ranked=[row for row in ranked if row['sku_id'] in sku_ids]
    for row in ranked:
        index=row['input_index'];original=candidates[index]
        if original['sku_id']!=row['sku_id']: raise ValueError('排名索引与规范 SKU 不一致')
        models.append({'sku_id':row['sku_id'],'model_id':row['model_id'],'variant_id':row.get('variant_id'),
            'name':row.get('display_name',row.get('产品名称')),'variant_name':row.get('variant_name'),
            'config':str(row.get('ram_raw') or '缺失')+' / '+str(row.get('storage_raw') or '缺失'),
            'score':row['score'],'price_id':row.get('price_id'),'condition':row.get('condition'),
            'observed_at':row.get('observed_at'),'is_estimated':row.get('is_estimated'),
            # Explanatory context only: these fields never enter the criterion matrix.
            'price_cny':original.get('price_cny'),'storage_gb':original.get('storage_gb'),
            'ram_gb':original.get('ram_gb')})
        for j,criterion in enumerate(di['criterion_definitions']):
            key=criterion['metric_key'];ev=di['provenance'][index]['criteria'][j]
            if ev['value']!=di['X'][index][j] or ev['metric_key']!=key:
                raise ValueError('矩阵和事实引用不一致')
            fact={**ev,'sku_id':row['sku_id'],'model_id':row['model_id'],'name':models[-1]['name'],
                  'label':LABELS.get(key,criterion.get('name_cn',key)),
                  'value':ev['value'],'sources':ev.get('sources',[]),
                  'dataset_version':result.dataset_version,'feature_rule_version':result.feature_rule_version,
                  'algorithm_version':result.algorithm_version,'weight':di['weights'][key]}
            if key.startswith('ref_'):
                # Bind the price snapshot to a citeable fact without adding a price
                # criterion or replacing the seven derived-reference facts.
                fact['model_context']={key:models[-1].get(key) for key in
                    ('price_cny','price_id','condition','observed_at','is_estimated','storage_gb','ram_gb')}
            if criterion['direction']=='target' and fact['value'] is not None:
                target=result.decision_context['target_screen']
                fact['transformation']={'rule':'abs(value - target)','target':target,
                    'deviation':abs(fact['value']-target),'direction':'cost'} if target is not None else None
            fact_id='F-'+digest(fact)[:24];facts[fact_id]={'fact_id':fact_id,**fact}
    bundle={'status':result.status,'reason':result.reason,'models':models,'facts':facts,
        'dataset_version':result.dataset_version,'feature_rule_version':result.feature_rule_version,
        'algorithm_version':result.algorithm_version,'needs':needs,'active_criteria':result.active_criteria,
        'decision_sha256':result.decision_sha256,'ranking_sha256':result.ranking_sha256,
        'decision_context':result.decision_context,
        'coverage_summary':di.get('coverage_summary',{}),
        'limitations':['TOPSIS 是当前可比候选集内的相对排序，不是绝对性能或购买概率。',
            '参数参考值不是实际体验；像素不等于拍照质量，电池容量不等于续航。',
            '游戏测试仅限对应游戏、协议、条件及适用范围，不能泛化为所有游戏。',
            '1% Low 当前无有效实测值；不推算、不填 0，不提供完整游戏总分。',
            '记录价格可能过时，二手约价不是新机现价。数据总体验收含待完成人工 / WPS 项。']}
    method=result.decision_context.get('method','topsis')
    if di.get('selection_rules',{}).get('evaluation_mode')=='spec_reference':
        bundle['limitations'][2]='游戏参考分由处理器80%与电池容量20%推导，不是游戏帧率、功耗、实测续航或完整游戏体验。'
        bundle['limitations'][3]='七维百分制采用固定锚点的初始设计，未由真实用户实验校准；缺失输入保留空值，有效0分不等于缺失。'
    bundle['method']=method
    bundle['score_label']='融合排序分' if method=='hybrid_rrf' else 'TOPSIS 贴近度'
    if method=='hybrid_rrf':
        bundle['limitations'][0]='混合排序（实验）是当前可比候选集内的融合排序，尚未证明更准确；不是绝对性能或购买概率。'
        bundle['limitations'].append('经典 TODIM 逆权重损失项可能与重要性直觉冲突；权重来自用户确认，非学习所得。')
    bundle['evidence_sha256']=digest(bundle)
    return bundle


def validate_fact_selection(response: dict,bundle: dict) -> list[str]:
    verify_bundle(bundle)
    if not isinstance(response,dict) or set(response)!={'fact_ids'}: raise ValueError('只接受事实 ID，不接受自由文案')
    ids=response['fact_ids']
    if (not isinstance(ids,list) or not ids or any(not isinstance(x,str) or x not in bundle['facts'] for x in ids)
            or len(set(ids))!=len(ids)):
        raise ValueError('事实 ID 无效或重复')
    return ids


def render_template(bundle: dict, fact_ids=None) -> str:
    verify_bundle(bundle)
    ids=list(bundle['facts']) if fact_ids is None else validate_fact_selection({'fact_ids':fact_ids},bundle)
    selected=set(ids)
    lines=[f"数据 {bundle['dataset_version']} / 规则 {bundle['feature_rule_version']} / 算法 {bundle['algorithm_version']}",bundle['reason']]
    context=bundle['decision_context']
    lines.append('已确认筛选与权重：'+json.dumps(context,ensure_ascii=False,allow_nan=False))
    for model in bundle['models']:
        lines.append(f"{model['name']} · {model['variant_name'] or '版本未记载'} · {model['config']} · {model['sku_id']}：{bundle.get('score_label','相对分')} {model['score']:.4f}")
        lines.append(f"价格快照 {model['price_id'] or '缺失'}；成色 {model['condition'] or '未知'}；记录日期 {model['observed_at'] or '未记载'}"+
                     ('；估计价，非实时成交价' if model['is_estimated'] else '；非实时价格承诺'))
        for ident,fact in bundle['facts'].items():
            if ident not in selected or fact['sku_id']!=model['sku_id']: continue
            value='缺失' if fact['value'] is None else f"{fact['value']:g} {fact.get('unit') or ''}".rstrip()
            group=fact.get('comparison_group') or '未选定可比组'
            lines.append(f"{fact['label']}：{value}；比较组 {group}；证据 {ident}")
            if fact.get('transformation'):
                t=fact['transformation']
                lines.append(f"屏幕目标 {t['target']:g} 英寸；绝对偏差 {t['deviation']:.4f} 英寸；偏差越小越好。")
    lines.extend(bundle['limitations'])
    return '\n'.join(lines)
