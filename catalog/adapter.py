"""Explicit adapters for catalog needs and the legacy offline DataFrame contract."""
import math
import pandas as pd
from .repository import CatalogRepository

CONTROL_MAP={'price':'price_cny','weight':'weight_g','storage':'storage_gb',
             'refresh':'refresh_rate_hz','screen':'screen_size_inch',
             'photo':'dxomark_photo','game':'honor_of_kings_avg_fps',
             'chipset':'chipset_tier'}
FRAME_FIELDS={'price':'price_cny','weight':'weight_g','storage':'storage_gb',
              'refresh':'refresh_rate_hz','screen':'screen_size_inch'}


def resolve_brands(requested,known):
    """Declared label aliases only, not automatic brand inference or fuzzy matching."""
    aliases=(('apple','苹果'),('huawei','华为'),('honor','荣耀'),('xiaomi','小米'),
             ('redmi','红米'),('realme','真我'),('oneplus','一加'),('samsung','三星'))
    resolved=[]
    for name in requested:
        group=next((set(pair) for pair in aliases if name.lower() in pair),{name.lower()})
        hits=[value for value in known if value.lower() in group]
        if len(hits)!=1:raise ValueError('品牌无法唯一匹配，请在手动条件中确认')
        if hits[0] not in resolved:resolved.append(hits[0])
    return resolved


def canonical_weights(weights):
    mapped={}
    for key,value in weights.items():
        canonical=CONTROL_MAP.get(key,key)
        if canonical in mapped: raise ValueError('同一指标的别名不能重复计权')
        mapped[canonical]=value
    return mapped


def build_input(repo, needs: dict, weights: dict[str,float], target_screen: float | None, mode: str):
    weights=canonical_weights(weights);budget=needs.get('budget') or {}
    brands=needs.get('brands') or {}
    includes=brands.get('include') or None if isinstance(brands,dict) else brands or None
    excludes=brands.get('exclude') or [] if isinstance(brands,dict) else []
    di=repo.build_decision_input(list(weights),mode=mode,weights=weights,
        min_price=budget.get('min'),max_price=budget.get('max'),
        min_storage_gb=needs.get('min_storage'),brands=includes,
        excluded_brands=needs.get('excluded_brands',excludes))
    di['selection_rules']['target_screen']=target_screen
    return di


def decision_input_from_frame(frame: pd.DataFrame, weights: dict[str,float]):
    if not frame.empty and ('id' not in frame or '产品名称' not in frame):
        raise ValueError('兼容夹具必须提供稳定 id 和独立机型 产品名称；不能用行数代替机型数')
    if not frame.empty and (frame['id'].isna().any() or not frame['id'].is_unique or frame['产品名称'].isna().any()):
        raise ValueError('兼容夹具身份缺失或歧义，不能自动合并')
    if not weights or set(weights).difference(FRAME_FIELDS): raise ValueError('兼容夹具只支持明确的五项控件')
    candidates=frame.copy();candidates['sku_id']=candidates.get('id',pd.Series(dtype=str))
    candidates['model_id']=candidates.get('model_id',candidates.get('产品名称',pd.Series(dtype=str)))
    definitions=[];X=[[] for _ in range(len(frame))]
    for key in weights:
        canonical=FRAME_FIELDS[key]
        definitions.append({'metric_key':canonical,'direction':'target' if key=='screen' else 'cost' if key in ('price','weight') else 'benefit'})
        values=frame[key].tolist() if key in frame else [None]*len(frame)
        for i,v in enumerate(values): X[i].append(None if pd.isna(v) else v)
    w=canonical_weights(weights);mask=[[v is None for v in row] for row in X]
    active=[j for j,d in enumerate(definitions) if w[d['metric_key']]>0]
    eligible=[i for i,row in enumerate(mask) if all(not row[j] for j in active)]
    status='no_candidates' if frame.empty else 'insufficient_comparable_data' if len(set(candidates.iloc[eligible]['model_id']))<2 else 'requires_target_transform' if w.get('screen_size_inch',0)>0 else 'ready'
    return {'candidates':candidates,'X':X,'criterion_definitions':definitions,'weights':w,
            'missing_mask':mask,'eligible_indices':eligible,'backup_indices':[i for i in range(len(frame)) if i not in eligible],
            'provenance':[{'sku_id':s,'criteria':[]} for s in candidates['sku_id']],
            'comparison_groups':{},'selected_run_ids':[{} for _ in X],'selection_rules':{'mode':'legacy_fixture'},
            'coverage_summary':{'candidate_count':len(frame)},'dataset_version':'legacy-offline-fixture',
            'feature_rule_version':'legacy-explicit-adapter','decision_status':status,
            'decision_reason':'兼容离线夹具；不足时不生成推荐成绩'}
