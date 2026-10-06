from pathlib import Path
import hashlib
import re
import numpy as np
import pandas as pd

SOURCE = Path(__file__).parent / 'data' / 'phones.xlsx'
FIELDS = {'运行内存':'ram','存储':'storage','重量':'weight','屏幕大小':'screen','屏幕刷新率':'refresh','价格':'price'}

# 稳定配置 ID 采用内容派生：同一 (产品名称, 运行内存, 存储) 永远得到同一 ID，
# 与行顺序无关。旧 ROW 编号取决于行顺序，新增或重排数据会整体漂移，
# 无法跨版本复算排序，也无法作为证据包的引用键。
# ROW_ID_LEGACY_PREFIX 仅用于测试断言「不再生成行号 ID」，不参与运行时逻辑。
SKU_PREFIX = 'SKU-'
ROW_ID_LEGACY_PREFIX = 'ROW-'
ID_COLUMNS = ('产品名称', '运行内存', '存储')

def number(value):
    match = re.search(r'\d+(?:\.\d+)?', str(value))
    return float(match.group()) if match else np.nan

def brand_of(name):
    for prefix, label in [('iPhone','苹果'),('Redmi','Redmi'),('小米','小米'),('荣耀','荣耀'),('华为','华为'),('三星','三星'),('vivo','vivo'),('OPPO','OPPO'),('一加','一加')]:
        if name.startswith(prefix): return label
    return '其他'

def stable_id(name, ram, storage):
    """由配置身份派生稳定 ID，与 Excel 行序无关。

    同型号不同存储得到不同 ID（工作文档第 3 章要求）；
    同一配置在不同版本的数据文件中保持同一 ID。
    """
    identity = '\u0001'.join(str(part).strip() for part in (name, ram, storage))
    digest = hashlib.sha1(identity.encode('utf-8')).hexdigest()[:8].upper()
    return SKU_PREFIX + digest

def load_data(source=SOURCE):
    frame = pd.read_excel(source)
    frame = frame.dropna(subset=['产品名称']).copy()
    for raw, key in FIELDS.items():
        frame[key] = frame[raw].map(number)
    frame.loc[frame['存储'].astype(str).str.contains('TB',case=False), 'storage'] *= 1024
    frame['brand'] = frame['产品名称'].map(brand_of)
    frame['id'] = [
        stable_id(row['产品名称'], row['运行内存'], row['存储'])
        for _, row in frame.iterrows()
    ]
    duplicates = frame['id'].duplicated()
    if duplicates.any():
        clashing = frame.loc[duplicates, list(ID_COLUMNS) + ['id']]
        raise ValueError(
            '配置身份不唯一，无法派生稳定 ID（产品名称 / 运行内存 / 存储 必须唯一）：\n'
            + clashing.to_string(index=False)
        )
    frame['配置'] = frame['运行内存'].astype(str)+' / '+frame['存储'].astype(str)
    frame['label'] = frame['产品名称']+' · '+frame['配置']+' · '+frame['id']
    return frame

ALGORITHM_VERSION='topsis-1.0.0'


def rank_decision_input(di, *, target_screen=None):
    """TOPSIS relative to this comparable candidate set; never a universal product score."""
    import math
    from numbers import Real
    from catalog.contracts import RankingResult
    from catalog.binding import decision_context, decision_fingerprint, ranking_fingerprint
    candidates=di['candidates'].copy()
    keys=[d['metric_key'] for d in di['criterion_definitions']]
    if not keys or len(set(keys))!=len(keys): raise ValueError('指标定义为空或重复')
    weights=di['weights']
    if set(weights)!=set(keys): raise ValueError('权重与指标定义不一致')
    if any(isinstance(v,bool) or not isinstance(v,Real) or not math.isfinite(v) or v<0 for v in weights.values()):
        raise ValueError('权重必须是有限非负数值')
    scale=max(weights.values())
    if scale<=0: raise ValueError('至少设置一个大于0的偏好权重')
    active=[j for j,k in enumerate(keys) if weights[k]>0]
    matrix=di['X']; masks=di['missing_mask']
    if len(matrix)!=len(candidates) or len(masks)!=len(candidates) or any(len(r)!=len(keys) for r in matrix+masks):
        raise ValueError('决策矩阵、缺失标记与候选尺寸不一致')
    if not candidates.empty and (not {'sku_id','model_id'}.issubset(candidates) or
            candidates['sku_id'].isna().any() or candidates['model_id'].isna().any() or not candidates['sku_id'].is_unique):
        raise ValueError('规范 SKU / 独立机型身份缺失或重复')
    eligible=di['eligible_indices']; backup=di['backup_indices']
    if (any(type(i) is not int or i<0 or i>=len(candidates) for i in eligible+backup)
            or len(set(eligible+backup))!=len(candidates) or set(eligible).intersection(backup)):
        raise ValueError('合格 / 备选索引必须唯一且完整覆盖候选')
    candidates['input_index']=range(len(candidates))
    backups=candidates.iloc[backup].copy()
    active_keys=[keys[j] for j in active]
    declared_target = di.get('selection_rules',{}).get('target_screen')
    if declared_target is not None and declared_target != target_screen:
        raise ValueError('排名屏幕目标与确认的决策输入不一致')
    input_hash = decision_fingerprint(di, target_screen)
    context = decision_context(di, target_screen)
    def result(status, ranked, reason):
        unscored = candidates.iloc[eligible].copy() if status!='ready' else candidates.iloc[:0].copy()
        output = RankingResult(status,ranked,backups,reason,di.get('dataset_version'),
                             di.get('feature_rule_version'),ALGORITHM_VERSION,active_keys,
                             unscored_candidates=unscored,decision_sha256=input_hash,decision_context=context)
        output.ranking_sha256=ranking_fingerprint(output)
        return output
    status=di['decision_status']
    if status not in ('ready','requires_target_transform','no_candidates','insufficient_comparable_data'):
        raise ValueError('未定义的决策状态')
    for i in eligible:
        for j in active:
            v=matrix[i][j]
            if masks[i][j] or v is None or isinstance(v,bool) or not isinstance(v,Real) or not math.isfinite(v):
                raise ValueError('合格候选的有效指标存在缺失或非法数值')
    if status in ('no_candidates','insufficient_comparable_data'):
        return result(status,candidates.iloc[:0].copy(),di['decision_reason'])
    if len(set(candidates.iloc[eligible]['model_id']))<2:
        return result('insufficient_comparable_data',candidates.iloc[:0].copy(),'可比的独立机型少于 2 个；不生成推荐分数')
    x=np.asarray([[matrix[i][j] for j in active] for i in eligible],dtype=float)
    directions=[di['criterion_definitions'][j]['direction'] for j in active]
    for column,direction in enumerate(directions):
        if direction=='target':
            if isinstance(target_screen,bool) or not isinstance(target_screen,Real) or not math.isfinite(target_screen) or target_screen<=0:
                raise ValueError('屏幕目标必须是有限正数，需用户确认')
            x[:,column]=np.abs(x[:,column]-target_screen)
            directions[column]='cost'
        elif direction not in ('cost','benefit'):
            raise ValueError('未知指标方向')
    if not np.isfinite(x).all(): raise ValueError('目标转换溢出')
    # Scale each column before vector normalization; x*x must never overflow.
    maxima=np.max(np.abs(x),axis=0)
    scaled=np.divide(x,maxima,out=np.zeros_like(x),where=maxima!=0)
    norms=np.linalg.norm(scaled,axis=0)
    normalized=np.divide(scaled,norms,out=np.zeros_like(scaled),where=norms!=0)
    w=np.asarray([weights[k]/scale for k in active_keys],dtype=float);w/=w.sum()
    weighted=normalized*w
    benefit=np.asarray([d=='benefit' for d in directions])
    high,low=weighted.max(axis=0),weighted.min(axis=0)
    ideal=np.where(benefit,high,low);worst=np.where(benefit,low,high)
    positive=np.linalg.norm(weighted-ideal,axis=1);negative=np.linalg.norm(weighted-worst,axis=1)
    scores=np.divide(negative,positive+negative,out=np.full(len(x),.5),where=(positive+negative)>0)
    ranked=candidates.iloc[eligible].copy();ranked['score']=scores
    ranked=ranked.sort_values(['score','sku_id'],ascending=[False,True],kind='stable')
    tied=bool(np.allclose(scores,scores[0],rtol=0,atol=1e-12))
    reason='有效维度无区分度，候选并列；ID 次序不是优劣' if tied else 'TOPSIS 当前可比候选集内相对排序；不代表手机绝对性能'
    return result('ready',ranked,reason)


def rank(frame, weights, target):
    """Legacy offline fixture adapter; production must pass a catalog DecisionInput."""
    if frame.empty: return frame.copy()
    from catalog.adapter import decision_input_from_frame
    result=rank_decision_input(decision_input_from_frame(frame,weights),target_screen=target)
    if result.status!='ready': raise ValueError(result.reason)
    return result.ranked
