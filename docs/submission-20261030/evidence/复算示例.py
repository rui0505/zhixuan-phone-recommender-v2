"""在已安装依赖的知选项目根目录执行。
python <本脚本路径>
人工构造算例，不代表真实手机、真实用户或效果提升。
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import pandas as pd
from catalog.adapter import decision_input_from_frame
from ranking_modes import rank

frame = pd.DataFrame([
    {'id': 'A', '产品名称': '构造方案A', 'weight': 175, 'storage': 256},
    {'id': 'B', '产品名称': '构造方案B', 'weight': 195, 'storage': 512},
    {'id': 'C', '产品名称': '构造方案C', 'weight': 220, 'storage': 1024},
    {'id': 'D', '产品名称': '构造方案D', 'weight': 185, 'storage': 256},
])
di = decision_input_from_frame(frame, {'weight': 7, 'storage': 3})
result = rank(di, method='hybrid_rrf')
columns = ['sku_id', 'topsis_score', 'todim_score', 'topsis_rank', 'todim_rank', 'score']
print(json.dumps(result.ranked[columns].to_dict('records'), ensure_ascii=False, indent=2))
