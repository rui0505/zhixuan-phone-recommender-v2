"""芯片档位：把 chipset 名称映射到 0-100 的综合性能分。

**这不是本站实测。** 分数来自第三方榜单换算，口径见 `data/chipset-tiers.json` 的
`basis` / `formula` / `sources` 字段：

- 极客湾手机 SoC 综合性能排行（CPU 70% + GPU 30%，骁龙 865 = 100，跨 iOS/Android/鸿蒙同尺度）
- `score = clamp(round(极客湾综合分 / 4), 1, 100)`
- 53 条中 30 条为实测换算，23 条为估算，逐条记录在 `estimated` 里

因此这个分数**只用于同一批候选内的相对排序**，不用于回答"这台手机性能多强"；
同分不代表同性能（散热、调度、屏幕差异往往大于 1~2 分）。

另外两件事必须做对：

1. **名称归一化**。库里同一颗芯片有多种写法（`第三代骁龙8` / `骁龙8Gen3`、
   `骁龙8 Gen2` / `骁龙8Gen2`），不归一化会有 26 个机型查不到分或被错分。
2. **缺分不能当 0 分**。查不到就返回 None，让该机型在这个维度上不可比，
   而不是给它一个"性能很差"的假分数。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

TABLE_NAME = 'chipset-tiers.json'

_CN_ORDINAL = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
               '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}

_CACHE = {}


def normalise_chipset(name):
    """把同一颗芯片的不同写法收敛到规范名。

    只处理有实际差异的两类：空格、中文序数。
    不认识的写法原样返回，由查表决定是否命中。
    """
    if not isinstance(name, str):
        return ''
    text = name.replace(' ', '').replace('\u3000', '')
    if not text:
        return ''
    # 第三代骁龙8 → 骁龙8Gen3；第二代骁龙7S → 骁龙7sGen2
    match = re.match(r'第(.)代骁龙(\d)([sS]?)(?:（.*）)?$', text)
    if match and match.group(1) in _CN_ORDINAL:
        return f'骁龙{match.group(2)}{match.group(3).lower()}Gen{_CN_ORDINAL[match.group(1)]}'
    # 第五代骁龙8至尊版forGalaxy → 骁龙8至尊版Gen5forGalaxy
    if '至尊版' in text:
        ordinal = re.match(r'第(.)代', text)
        generation = (f'Gen{_CN_ORDINAL[ordinal.group(1)]}'
                      if ordinal and ordinal.group(1) in _CN_ORDINAL else '')
        galaxy = 'forGalaxy' if 'forGalaxy' in text else ''
        return f'骁龙8至尊版{generation}{galaxy}'
    return text


def table_path(db_path):
    """档位表与数据库放在一起，跟着同一次发布走。"""
    return Path(db_path).resolve().with_name(TABLE_NAME)


def load_table(path):
    """读档位表。返回 `(scores, meta)`；表缺失或格式不对就抛错，不静默降级。"""
    path = Path(path)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError as exc:
        raise ValueError(f'缺少芯片档位表：{path.name}') from exc
    cached = _CACHE.get(str(path))
    if cached is not None and cached[0] == stamp:
        return cached[1], cached[2]
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'芯片档位表无法解析：{path.name}') from exc
    scores = document.get('scores')
    if not isinstance(scores, dict) or not scores:
        raise ValueError('芯片档位表缺少 scores')
    for name, value in scores.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'芯片档位必须是数值：{name}')
        if not 0 < value <= 100:
            raise ValueError(f'芯片档位超出 0-100：{name}={value}')
    meta = {key: document.get(key) for key in
            ('version', 'generated_at', 'scale', 'basis', 'formula', 'not',
             'disclosure', 'accuracy', 'sources', 'estimated')}
    meta['estimated'] = set(meta.get('estimated') or ())
    _CACHE[str(path)] = (stamp, scores, meta)
    return scores, meta


def tier_for(chipset, scores):
    """查芯片档位分。查不到返回 None——缺分不能当 0 分。"""
    if not scores:
        return None
    return scores.get(normalise_chipset(chipset))

