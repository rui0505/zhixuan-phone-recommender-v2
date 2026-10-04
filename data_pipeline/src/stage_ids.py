# -*- coding: utf-8 -*-
"""阶段二：稳定 ID 注册表与别名表。

ID 分配规则（ids-1.0.0）：
  model_id   按产品名称在源表中首次出现的顺序编号 M0001..；名称变化、行重排不改变已分配 ID。
  variant_id 每个机型至少一个基础版本；源表「版本/备注」明确写出的硬件版本另建 V 记录。
  sku_id     每个源数据行一条，键为 (variant_id, 运行内存原文, 存储原文)，保留内存类型差异。

id_registry.csv 是长期映射文件：重复运行复用已有 ID，只追加新键，从不回收或重编号。
"""
from __future__ import annotations

import os

from common import normalize_match_key, now_iso, write_csv_bom

# 品牌前缀表：按最长前缀匹配，未匹配记为 NULL 并登记问题。
BRAND_PREFIXES = [
    ("iPhone", "Apple"),
    ("三星Galaxy", "三星"),
    ("三星", "三星"),
    ("华为", "华为"),
    ("Redmi", "Redmi"),
    ("小米", "小米"),
    ("荣耀", "荣耀"),
    ("vivo", "vivo"),
    ("iQOO", "iQOO"),
    ("OPPO", "OPPO"),
    ("一加", "一加"),
    ("realme", "realme"),
]

REGISTRY_HEADER = ["id", "id_type", "registry_key", "parent_id", "display_name",
                   "match_key", "ram_raw", "storage_raw", "memory_type",
                   "source_record_id", "assigned_at", "rule_version"]
ALIAS_HEADER = ["alias_key", "canonical_model_id", "model_name_raw", "match_key",
                "alias_type", "source_record_id", "note"]


def detect_brand(name: str):
    if not name:
        return None
    for prefix, brand in BRAND_PREFIXES:
        if str(name).startswith(prefix):
            return brand
    return None


def load_registry(path: str) -> dict:
    """读取已有注册表；返回 registry_key -> id。"""
    import csv
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return {row["registry_key"]: row["id"] for row in csv.DictReader(fh)}


class IdAllocator:
    def __init__(self, existing: dict, prefix: str, width: int = 4):
        self.existing = dict(existing)
        self.prefix = prefix
        self.width = width
        self.rows = []
        self._max = 0
        for v in self.existing.values():
            if isinstance(v, str) and v.startswith(prefix):
                try:
                    self._max = max(self._max, int(v[len(prefix):]))
                except ValueError:
                    pass
        self.created = 0

    def get(self, registry_key: str, display_name: str, parent_id, source_record_id,
            match_key="", ram_raw="", storage_raw="", memory_type="", rule_version=""):
        if registry_key in self.existing:
            return self.existing[registry_key]
        self._max += 1
        self.created += 1
        new_id = f"{self.prefix}{self._max:0{self.width}d}"
        self.existing[registry_key] = new_id
        self.rows.append({
            "id": new_id,
            "id_type": {"M": "model", "V": "variant", "S": "sku"}[self.prefix],
            "registry_key": registry_key,
            "parent_id": parent_id or "",
            "display_name": display_name,
            "match_key": match_key,
            "ram_raw": ram_raw,
            "storage_raw": storage_raw,
            "memory_type": memory_type or "",
            "source_record_id": source_record_id,
            "assigned_at": now_iso(),
            "rule_version": rule_version,
        })
        return new_id


def build_ids(main_rows: list, registry_path: str, alias_path: str, rule_version: str,
              id_registry_version: str):
    """main_rows: [{'row_no':int, '产品名称':..., '运行内存':..., '存储':..., '版本/备注':...}]

    返回 (models, variants, skus, registry_rows, alias_rows, issues)
    """
    existing = load_registry(registry_path)
    models = IdAllocator(existing, "M")
    variants = IdAllocator(existing, "V")
    skus = IdAllocator(existing, "S")
    issues = []

    model_index = {}    # match_key -> model dict
    variant_index = {}  # (model_id, variant_key) -> variant dict
    model_order = []
    alias_rows = []

    for r in main_rows:
        name = r.get("产品名称")
        name_key = normalize_match_key(name)
        if not name_key:
            issues.append(dict(issue_code="EMPTY_PRODUCT_NAME", severity="high",
                               source_record_id=r["source_record_id"],
                               detail="产品名称为空，无法建立 model_id"))
            continue

        if name_key not in model_index:
            mid = models.get(f"model|{name_key}", name, None, r["source_record_id"],
                             match_key=name_key, rule_version=rule_version)
            brand = detect_brand(name)
            if brand is None:
                issues.append(dict(issue_code="BRAND_UNRECOGNIZED", severity="low",
                                   source_record_id=r["source_record_id"],
                                   detail=f"产品名称 {name!r} 未匹配品牌前缀表，brand 记为 NULL"))
            m = {"model_id": mid, "brand": brand, "display_name": name,
                 "match_key": name_key, "first_row": r["row_no"],
                 "source_record_id": r["source_record_id"], "variants": []}
            model_index[name_key] = m
            model_order.append(m)
            alias_rows.append({
                "alias_key": f"src_name|{name_key}", "canonical_model_id": mid,
                "model_name_raw": name, "match_key": name_key, "alias_type": "source_name",
                "source_record_id": r["source_record_id"],
                "note": "源表产品名称，逐值原样登记",
            })
        m = model_index[name_key]

        # ---- 版本 ----
        ver_raw = (r.get("版本/备注") or "").strip()
        sat = 1 if "卫星" in ver_raw else 0
        variant_key = "satellite" if sat else "base"
        vkey = (m["model_id"], variant_key)
        if vkey not in variant_index:
            vname = "卫星通信版" if sat else "标准版"
            region = "中国大陆" if "中国大陆" in str(r.get("新增配置备注") or "") else None
            vid = variants.get(
                f"variant|{name_key}|{variant_key}", vname, m["model_id"],
                r["source_record_id"], match_key=name_key, rule_version=rule_version)
            v = {"variant_id": vid, "model_id": m["model_id"], "variant_name": vname,
                 "variant_key": variant_key, "satellite_support": sat,
                 "region": region, "is_default": 0 if sat else 1,
                 "name_is_source": 1 if sat else 0,
                 "variant_note_raw": ver_raw, "source_record_id": r["source_record_id"],
                 "skus": []}
            variant_index[vkey] = v
            m["variants"].append(v)
            if sat:
                issues.append(dict(issue_code="VARIANT_SATELLITE_KEPT_SEPARATE", severity="info",
                                   source_record_id=r["source_record_id"],
                                   detail=f"{name} 存在「{ver_raw}」，已建立独立 variant_id={vid}，"
                                          f"不与标准版自动合并"))
        v = variant_index[vkey]
        if ver_raw and not sat:
            issues.append(dict(issue_code="VERSION_NOTE_NON_VARIANT", severity="info",
                               source_record_id=r["source_record_id"],
                               detail=f"版本/备注={ver_raw!r} 属于价格限定条件而非硬件版本，"
                                      f"已保留在价格快照而不新建 variant"))

        # ---- 配置 ----
        ram_raw = (r.get("运行内存") or "").strip()
        sto_raw = (r.get("存储") or "").strip()
        skey = f"sku|{name_key}|{variant_key}|{ram_raw}|{sto_raw}"
        sid = skus.get(skey, f"{name} {v['variant_name']} {ram_raw}/{sto_raw}",
                       v["variant_id"], r["source_record_id"], match_key=name_key,
                       ram_raw=ram_raw, storage_raw=sto_raw, rule_version=rule_version)
        s = {"sku_id": sid, "variant_id": v["variant_id"], "model_id": m["model_id"],
             "ram_raw": ram_raw, "storage_raw": sto_raw,
             "source_record_id": r["source_record_id"], "row_no": r["row_no"]}
        v["skus"].append(s)
        r["_model_id"] = m["model_id"]
        r["_variant_id"] = v["variant_id"]
        r["_sku_id"] = sid

    registry_rows = models.rows + variants.rows + skus.rows

    # 追加写入（不覆盖历史）
    append_registry(registry_path, models, variants, skus)
    write_csv_bom(alias_path, ALIAS_HEADER,
                  [[a[h] for h in ALIAS_HEADER] for a in alias_rows])

    stats = {"models": len(model_order), "variants": len(variant_index),
             "skus": sum(len(v["skus"]) for v in variant_index.values()),
             "new_models": models.created, "new_variants": variants.created,
             "new_skus": skus.created, "id_registry_version": id_registry_version}
    return model_order, list(variant_index.values()), registry_rows, alias_rows, issues, stats


def append_registry(path: str, *allocators) -> None:
    """把新分配的 ID 追加到注册表；表不存在则先写表头。"""
    import csv
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        if not exists:
            w.writerow(REGISTRY_HEADER)
        for a in allocators:
            for row in a.rows:
                w.writerow([row.get(h, "") for h in REGISTRY_HEADER])


def select_pilot_ids(rows: list[dict], registry: list[dict], n: int = 20) -> list[str]:
    registered = {r['id'] for r in registry if r.get('id_type') == 'sku'}
    unique = {r['_sku_id']: r for r in rows if r.get('_sku_id') in registered}
    if len(unique) < n:
        raise ValueError('Not enough registered SKUs for the requested pilot')
    chosen = []
    ordered = sorted(unique.items())
    cases = [('realme', 'GT5'), ('荣耀', 'X70'), ('华为', 'Mate80Pro')]
    for tokens in cases:
        case_name = ''.join(tokens).casefold()
        matches = [(sid, row) for sid, row in ordered if case_name ==
                   str(row.get('产品名称', '')).replace(' ', '').casefold()]
        # Cover each known configuration variation rather than only a model's first row.
        for sid, _ in matches:
            if sid not in chosen and len(chosen) < n:
                chosen.append(sid)
    for sid, row in ordered:
        if '卫星' in str(row.get('版本/备注') or '') and sid not in chosen and len(chosen) < n:
            chosen.append(sid)
            for standard_id, standard in ordered:
                if (standard.get('产品名称') == row.get('产品名称')
                        and standard.get('运行内存') == row.get('运行内存')
                        and standard.get('存储') == row.get('存储')
                        and '卫星' not in str(standard.get('版本/备注') or '')
                        and standard_id not in chosen and len(chosen) < n):
                    chosen.append(standard_id)
                    break
    brands = {detect_brand(unique[sid].get('产品名称')) for sid in chosen}
    for sid, row in ordered:
        brand = detect_brand(row.get('产品名称'))
        if brand not in brands and sid not in chosen and len(chosen) < n:
            chosen.append(sid)
            brands.add(brand)
    for sid, _ in ordered:
        if sid not in chosen and len(chosen) < n:
            chosen.append(sid)
    return sorted(chosen)
