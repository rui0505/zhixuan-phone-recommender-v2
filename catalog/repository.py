# -*- coding: utf-8 -*-
"""网页读取适配器（repository.py）。

职责：只读访问 catalog.sqlite，向网页层返回结构化数据与决策输入。
路径全部通过参数注入，不写死任何绝对路径。

返回值优先为 pandas.DataFrame；环境无 pandas 时回退为 list[dict]，调用方可用
``as_records()`` 统一处理。
"""
from __future__ import annotations

import json
import os
import sqlite3
import math
import hashlib
from datetime import date
from numbers import Real
from pathlib import Path

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.sqlite")

# 价格选择优先级：同一 sku 同一模式下的取值顺序
PRICE_PRIORITY_NEW = ["official_current_price", "official_launch_price", "marketplace_listing_price"]
PRICE_PRIORITY_USED = ["used_market_estimate"]


class CatalogRepository:
    def __init__(self, db_path: str = None, derived_asset_root: str = None):
        self.db_path = os.path.abspath(db_path or DEFAULT_DB)
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"目录库不存在：{self.db_path}（请先运行 build_dataset.py）")
        self.derived_asset_root = os.path.abspath(
            derived_asset_root or os.path.join(os.path.dirname(self.db_path), "assets_derived"))

    # ------------------------------------------------------------ 基础
    def _q(self, sql, params=()):
        con = sqlite3.connect(Path(self.db_path).as_uri() + '?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()
        return pd.DataFrame(rows) if pd is not None else rows

    @staticmethod
    def as_records(obj):
        """DataFrame -> list[dict]，并把 NaN 还原为 None。

        SQLite 的 NULL 经 pandas 会变成 NaN，直接使用会把「缺失」误显示成 nan，
        也破坏了 NULL 与 0 的区别，因此统一在此还原。
        """
        if pd is not None and isinstance(obj, pd.DataFrame):
            return obj.astype(object).where(obj.notna(), None).to_dict("records")
        return obj

    def dataset_version(self) -> dict:
        rows = self.as_records(self._q(
            "SELECT * FROM dataset_releases ORDER BY built_at DESC LIMIT 1"))
        return rows[0] if rows else {}

    def get_spec_provenance(self, spec_id: str) -> list[dict]:
        return self.as_records(self._q('SELECT * FROM spec_provenance WHERE spec_id=? '
                                      'ORDER BY source_record_id,source_col', (spec_id,)))

    def resolve_legacy_sku_id(self, legacy_id: str) -> dict:
        candidates = self.as_records(self._q('SELECT s.sku_id,m.display_name,s.ram_raw,s.storage_raw '
                                             'FROM skus s JOIN phone_models m USING(model_id) ORDER BY s.sku_id'))
        matched = []
        for row in candidates:
            identity = '\x01'.join(str(row[key]).strip() for key in ('display_name', 'ram_raw', 'storage_raw'))
            old = 'SKU-' + hashlib.sha1(identity.encode('utf-8')).hexdigest()[:8].upper()
            if old == legacy_id:
                matched.append(row['sku_id'])
        return {'status': 'unique' if len(matched) == 1 else 'ambiguous' if matched else 'not_found',
                'sku_ids': matched}

    # ------------------------------------------------------------ 目录
    def load_catalog(self, brand=None, mode="all", min_storage_gb=None, max_price=None, *,
                     min_price=None, excluded_brands=None, price_kind_preference=None):
        """机型目录：一行一个 sku，含机型/版本/配置/选定价格/封面图。"""
        _validate_mode(mode)
        _validate_nonnegative('min_storage_gb', min_storage_gb)
        _validate_nonnegative('max_price', max_price)
        _validate_nonnegative('min_price', min_price)
        if min_price is not None and max_price is not None and min_price > max_price:
            raise ValueError('min_price 不能大于 max_price')
        _validate_brand_list('excluded_brands', excluded_brands)
        sql = """
        SELECT m.model_id, m.brand, m.display_name, m.release_month,
               v.variant_id, v.variant_name, v.region, v.satellite_support, v.is_default,
               s.sku_id, s.ram_raw, s.ram_gb, s.storage_raw, s.storage_gb, s.memory_type,
               p.price_id, p.amount AS price_cny, p.currency, p.price_kind, p.condition,
               p.is_estimated, p.channel, p.price_note, p.source_sale_status,
               p.observed_at, p.source_record_id AS price_source_record_id, p.source_id AS price_source_id,
               s.source_record_id AS sku_source_record_id,
               m.cover_asset_id
        FROM skus s
        JOIN phone_variants v ON v.variant_id = s.variant_id
        JOIN phone_models m ON m.model_id = s.model_id
        LEFT JOIN price_snapshots p ON p.sku_id = s.sku_id
        """
        rows = self.as_records(self._q(sql))
        out = []
        for r in rows:
            if brand and r["brand"] != brand:
                continue
            if excluded_brands and r['brand'] in excluded_brands:
                continue
            if min_storage_gb and (r["storage_gb"] or 0) < min_storage_gb:
                continue
            out.append(r)
        out = _select_single_price(out, mode, price_kind_preference)
        for r in out:
            if (r.get('price_cny') is not None and (not math.isfinite(r['price_cny'])
                    or r['price_cny'] < 0 or r.get('currency') != 'CNY')):
                r['price_cny'] = None  # Never infer exchange rates or replace the chosen snapshot.
        if mode in ("new", "used"):
            # 新机/二手模式：只保留该模式下确有价格的配置，不静默混入另一类
            out = [r for r in out if r.get("price_cny") is not None]
        if max_price is not None:
            out = [r for r in out if r.get("price_cny") is not None and r["price_cny"] <= max_price]
        if min_price is not None:
            out = [r for r in out if r.get('price_cny') is not None and r['price_cny'] >= min_price]
        # 一个 sku 只保留一行
        seen, dedup = set(), []
        for r in out:
            if r["sku_id"] in seen:
                continue
            seen.add(r["sku_id"])
            dedup.append(r)
        dedup.sort(key=lambda r: r['sku_id'])
        return pd.DataFrame(dedup) if pd is not None else dedup

    def list_models(self):
        return self._q("SELECT model_id, brand, display_name, release_month, cover_asset_id "
                       "FROM phone_models ORDER BY model_id")

    def resolve_model_id(self, name_or_id: str):
        if not name_or_id:
            return None
        rows = self.as_records(self._q(
            "SELECT model_id FROM phone_models WHERE model_id=? OR display_name=? "
            "OR display_name_match_key=?", (name_or_id, name_or_id,
                                            _key(name_or_id))))
        return rows[0]["model_id"] if rows else None

    # ------------------------------------------------------------ 详情
    def get_model_details(self, model_id: str = None, model_name: str = None,
                          mode="all", include_missing=True):
        _validate_mode(mode)
        mid = model_id or self.resolve_model_id(model_name)
        if not mid:
            return None
        info = self.as_records(self._q(
            "SELECT * FROM phone_models WHERE model_id=?", (mid,)))
        variants = self.as_records(self._q(
            "SELECT * FROM phone_variants WHERE model_id=? ORDER BY variant_id", (mid,)))
        skus = self.as_records(self._q(
            "SELECT * FROM skus WHERE model_id=? ORDER BY sku_id", (mid,)))
        prices = self.as_records(self._q(
            "SELECT p.* FROM price_snapshots p JOIN skus s ON s.sku_id=p.sku_id "
            "WHERE s.model_id=? ORDER BY p.price_id", (mid,)))
        specs = self.as_records(self._q(
            "SELECT * FROM specifications WHERE (subject_type='model' AND subject_id=?) "
            "OR (subject_type='variant' AND subject_id IN (SELECT variant_id FROM phone_variants WHERE model_id=?)) "
            "OR (subject_type='sku' AND subject_id IN (SELECT sku_id FROM skus WHERE model_id=?)) "
            "ORDER BY spec_id", (mid, mid, mid)))
        runs = self.as_records(self._q(
            "SELECT * FROM benchmark_runs WHERE model_id=? ORDER BY run_id", (mid,)))
        obs = self.as_records(self._q(
            "SELECT o.* FROM metric_observations o JOIN benchmark_runs r ON r.run_id=o.run_id "
            "WHERE r.model_id=? ORDER BY o.observation_id", (mid,)))
        assets = self.get_assets(mid)
        snippets = self.as_records(self.get_evidence_snippets(mid))
        if mode in ("new", "used"):
            prices = [p for p in prices if p["condition"] == mode]
            keep = {p["sku_id"] for p in prices}
            skus = [s for s in skus if s["sku_id"] in keep]
        if not include_missing:
            obs = [o for o in obs if o["value_num"] is not None]
        return {"model": info[0] if info else None, "variants": variants, "skus": skus,
                "prices": prices, "specifications": specs, "benchmark_runs": runs,
                "observations": obs, "assets": assets, "evidence_snippets": snippets,
                "missing": [o for o in obs if o["value_num"] is None]}

    # ------------------------------------------------------------ 图片
    def get_assets(self, model_id: str = None, color_id: str = None,
                   include_derived_path=True):
        sql = "SELECT * FROM assets WHERE 1=1"
        params = []
        if model_id:
            sql += " AND model_id=?"
            params.append(model_id)
        if color_id:
            sql += " AND color_id=?"
            params.append(color_id)
        sql += " ORDER BY asset_id"
        rows = self.as_records(self._q(sql, tuple(params)))
        for r in rows:
            if include_derived_path and r.get("derived_path"):
                r["derived_abs"] = os.path.join(self.derived_asset_root,
                                                *str(r["derived_path"]).split("/"))
            else:
                r["derived_abs"] = None
        return pd.DataFrame(rows) if pd is not None else rows

    def get_colors(self, model_id: str):
        return self._q("SELECT * FROM asset_colors WHERE model_id=? ORDER BY color_id",
                       (model_id,))

    # ------------------------------------------------------------ 指标
    def get_observations(self, model_id: str = None, metric_keys=None,
                         comparison_group=None, include_missing=True):
        sql = """
        SELECT o.*, r.model_id, r.benchmark, r.institution, r.protocol, r.protocol_detail,
               r.run_date, r.applicable_scope, r.scope_note, r.source_id, r.run_id,
               m.display_name
        FROM metric_observations o
        JOIN benchmark_runs r ON r.run_id = o.run_id
        JOIN phone_models m ON m.model_id = r.model_id
        WHERE 1=1
        """
        params = []
        if model_id:
            sql += " AND r.model_id=?"
            params.append(model_id)
        if metric_keys:
            sql += " AND o.metric_key IN (%s)" % ",".join("?" * len(metric_keys))
            params.extend(metric_keys)
        if comparison_group:
            sql += " AND o.comparison_group=?"
            params.append(comparison_group)
        if not include_missing:
            sql += " AND o.value_num IS NOT NULL"
        return self._q(sql, tuple(params))

    def get_metric_definitions(self):
        return self._q("SELECT * FROM metric_definitions ORDER BY metric_key")

    def get_evidence_snippets(self, model_id: str = None, snippet_kind: str = None,
                             tag: str = None, run_id: str = None):
        """评测摘录与检索标签。

        返回的 origin/extraction_method/tag_method 用于区分：
        - origin=source_table_human：源表里人工撰写的原文
        - tags 由 tag_method=keyword_rule_v1 自动提取，不是人工结论
        """
        sql = ("SELECT s.*, src.raw_text AS source_raw_text, src.url AS source_url "
               "FROM evidence_snippets s LEFT JOIN sources src ON src.source_id = s.source_id "
               "WHERE 1=1")
        params = []
        if model_id:
            sql += " AND s.subject_id=?"
            params.append(model_id)
        if snippet_kind:
            sql += " AND s.snippet_kind=?"
            params.append(snippet_kind)
        if run_id:
            sql += " AND s.related_run_id=?"
            params.append(run_id)
        if tag:
            sql += " AND (',' || s.tags || ',') LIKE ?"
            params.append(f"%,{tag},%")
        sql += " ORDER BY s.snippet_id"
        return self._q(sql, tuple(params))

    def search_text(self, keyword: str, limit: int = 50):
        """第一版文字检索：SQL LIKE 关键词命中摘录原文或标签。"""
        kw = f"%{keyword}%"
        return self._q(
            "SELECT snippet_id, subject_id, snippet_kind, snippet_text, tags "
            "FROM evidence_snippets WHERE snippet_text LIKE ? OR tags LIKE ? "
            "ORDER BY snippet_id LIMIT ?", (kw, kw, limit))

    def get_provenance(self, sku_id: str):
        """返回该 sku 的价格与全部可用证据链。"""
        rows = self.as_records(self._q(
            "SELECT p.*, src.raw_text AS source_raw_text, src.url AS source_url "
            "FROM price_snapshots p LEFT JOIN sources src ON src.source_id=p.source_id "
            "WHERE p.sku_id=?", (sku_id,)))
        return rows

    # ------------------------------------------------------------ 决策输入
    def build_decision_input(self, metric_keys=None, mode="new", weights=None,
                             max_price=None, min_storage_gb=None, brands=None,
                             price_kind_preference=None, *, min_price=None, excluded_brands=None,
                             comparison_group_overrides=None):
        """只构建可比输入，不计算 TOPSIS/VIKOR 分数。先检查 decision_status。"""
        from .decision_input import build_decision_input
        return build_decision_input(self, metric_keys, mode, weights, max_price,
                                    min_storage_gb, brands, price_kind_preference, min_price=min_price,
                                    excluded_brands=excluded_brands,
                                    comparison_group_overrides=comparison_group_overrides)

    def summary(self) -> dict:
        counts = {}
        for t in ("phone_models", "phone_variants", "skus", "price_snapshots",
                  "specifications", "benchmark_runs", "metric_observations", "sources",
                  "assets", "quality_issues"):
            counts[t] = self._q(f"SELECT COUNT(*) n FROM {t}").iloc[0]["n"] \
                if pd is not None else self._q(f"SELECT COUNT(*) n FROM {t}")[0]["n"]
        return counts


# ---------------------------------------------------------------- 价格选择

def _validate_mode(mode):
    if mode not in ('new', 'used', 'all'):
        raise ValueError('mode 必须为 new、used 或 all，不自动退回全量模式')


def _validate_nonnegative(name, value):
    if value is not None and (isinstance(value, bool) or not isinstance(value, Real)
                              or not math.isfinite(value) or value < 0):
        raise ValueError(f'{name} 必须为有限、非负数值')

def _validate_brand_list(name, value):
    if value is not None and (not isinstance(value, (list, tuple, set)) or
                             not all(isinstance(x, str) for x in value)):
        raise ValueError(f'{name} 必须为品牌名称列表')


def known_date_key(value):
    """ISO date only; unknown is older, month precision remains month precision."""
    if not isinstance(value, str):
        return (0, '')
    try:
        if len(value) == 7:
            date.fromisoformat(value + '-01')
        else:
            date.fromisoformat(value)
        return (1, value)
    except ValueError:
        return (0, '')


def _select_single_price(rows, mode, price_kind_preference=None):
    """同一 sku 在同一模式下只保留一条价格。

    模式语义：
      new  -> 只取 condition='new' 的价格（官方在售价等）
      used -> 只取 condition='used' 的价格（二手行情约价）
      all  -> 按 price_kind 优先级择优，成色不限
    condition='unknown'（无成色证据的电商报价）不会静默进入 new 或 used；
    某模式无符合成色的价格时，价格字段留空并注明原因，不退回其他成色。
    同 kind 下取 observed_at 最新，再按 price_id 升序，保证可复现。
    """
    _validate_mode(mode)
    if mode == "new":
        priority, allowed = PRICE_PRIORITY_NEW, {"new"}
    elif mode == "used":
        priority, allowed = PRICE_PRIORITY_USED, {"used"}
    else:
        priority, allowed = (PRICE_PRIORITY_NEW + PRICE_PRIORITY_USED), None
    if price_kind_preference is not None:
        if (not isinstance(price_kind_preference, (list, tuple)) or not price_kind_preference
                or not all(isinstance(k, str) and k in priority for k in price_kind_preference)
                or len(set(price_kind_preference)) != len(price_kind_preference)):
            raise ValueError('price_kind_preference 必须为当前模式已定义价格口径的无重复列表')
        priority = list(price_kind_preference) + [k for k in priority if k not in price_kind_preference]

    grouped = {}
    for r in rows:
        grouped.setdefault(r["sku_id"], []).append(r)
    out = []
    for sku_id, items in grouped.items():
        priced = [i for i in items if i.get("price_id")]
        if not priced:
            base = dict(items[0])
            for k in ("price_id", "price_cny", "currency", "price_kind", "condition",
                      "is_estimated", "channel", "price_note", "observed_at",
                      "price_source_record_id", "source_sale_status"):
                base.setdefault(k, None)
            out.append(base)
            continue
        cands = [p for p in priced if allowed is None or p.get("condition") in allowed]
        if not cands:
            base = dict(items[0])
            for k in ("price_id", "price_cny", "currency", "price_kind", "is_estimated",
                      "channel", "observed_at", "price_source_record_id"):
                base[k] = None
            base["condition"] = None
            base["price_note"] = f"该配置没有 condition={mode} 的价格记录"
            out.append(base)
            continue

        def rank_of(p):
            try:
                return priority.index(p.get("price_kind"))
            except ValueError:
                return len(priority)

        # 稳定多级排序：价格类型优先级 -> 采集日期（新在前）-> price_id（小在前）
        cands = sorted(cands, key=lambda p: str(p.get("price_id")))
        cands = sorted(cands, key=lambda p: known_date_key(p.get('observed_at')), reverse=True)
        cands = sorted(cands, key=rank_of)
        out.append(cands[0])
    return out


def _key(s):
    """匹配键规范化：统一空白、大小写、全半角。只用于匹配，不改变原文。

    内联实现，避免网页部署时依赖构建期的 src/ 模块。
    """
    if s is None:
        return ""
    out = []
    for ch in str(s):
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out).strip().lower()
