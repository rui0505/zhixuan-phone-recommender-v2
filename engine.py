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

def rank(frame, weights, target):
    if frame.empty: return frame.copy()
    keys = ['price','weight','storage','refresh','screen']
    w = np.array([weights[k] for k in keys],dtype=float)
    if np.any(w<0) or w.sum()<=0: raise ValueError('至少设置一个大于0的偏好权重。')
    x = frame[keys].to_numpy(dtype=float, copy=True)
    if not np.isfinite(x).all(): raise ValueError('候选数据存在缺失或无效数值，不能计算。')
    # Target dimension becomes distance from preferred size; smaller is better.
    x[:,-1] = np.abs(x[:,-1]-target)
    norms = np.linalg.norm(x,axis=0)
    weighted = np.divide(x,norms,out=np.zeros_like(x),where=norms!=0) * (w/w.sum())
    benefit = np.array([False,False,True,True,False])
    high, low = weighted.max(axis=0), weighted.min(axis=0)
    ideal = np.where(benefit,high,low)
    worst = np.where(benefit,low,high)
    positive = np.linalg.norm(weighted-ideal,axis=1)
    negative = np.linalg.norm(weighted-worst,axis=1)
    score = np.divide(negative,positive+negative,out=np.full(len(x),.5),where=(positive+negative)>1e-12)
    out = frame.copy(); out['score']=score
    return out.sort_values(['score','price','id'],ascending=[False,True,True])
