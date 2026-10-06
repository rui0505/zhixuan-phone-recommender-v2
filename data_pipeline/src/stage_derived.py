# -*- coding: utf-8 -*-
"""阶段三：派生字段、评测运行与指标观测。

设计要点：
- 每条规范记录都带 source_record_id / raw_value，派生值与原文并存。
- 解析不出精确值一律留 NULL 并登记问题，不做近似、不填平均数、不按近似机型推算。
- 评测运行先按 (对象, 协议, 条件, 来源) 归并，再挂指标观测，避免同一实测被多配置重复计数。
"""
from __future__ import annotations

import json
import re

from common import normalize_match_key
from rules import (MISSING_NOT_FOUND, MISSING_NOT_REPORTED, PRICE_KIND_MAP,
                   PRICE_CONDITION_UNKNOWN, RULE_VERSION, SNIPPET_KIND_METRICS,
                   TAG_RULE_VERSION, classify_missing, comparison_group,
                   extract_source_date, extract_tags, parse_number, parse_price,
                   parse_ram_gb, parse_release_month, parse_storage, parse_tristate,
                   spec_comparison_group)

URL_RE = re.compile(r"https?://[^\s，；、）)\"'<>]+")


class SourceRegistry:
    """来源登记：按 (kind, raw_text) 去重，稳定编号 SRC0001.."""

    def __init__(self, collected_at: str):
        self.collected_at = collected_at
        self.by_key = {}
        self.rows = []

    def get(self, raw_text, kind: str, default_publisher: str = None):
        raw = (raw_text or "").strip()
        if not raw or raw in ("—", "-", "--"):
            return None
        key = (kind, raw)
        if key in self.by_key:
            return self.by_key[key]
        sid = f"SRC{len(self.rows) + 1:04d}"
        urls = URL_RE.findall(raw)
        published, granularity = extract_source_date(raw)
        self.by_key[key] = sid
        self.rows.append({
            "source_id": sid, "source_kind": kind, "raw_text": raw,
            "url": "\n".join(urls) if urls else "",
            "url_count": len(urls),
            "publisher": default_publisher or "",
            "published_at": published or "",
            "published_granularity": granularity,
            "collected_at": self.collected_at,
            "url_is_parseable": 1 if urls else 0,
        })
        return sid


# ---------------------------------------------------------------- 规格字段

def parse_weight(raw):
    if raw is None:
        return None, "missing"
    s = str(raw).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*g", s, flags=re.IGNORECASE)
    if m:
        return float(m.group(1)), "ok"
    return None, ("missing" if classify_missing(s) else "unparsed")


def parse_screen_size(raw):
    if raw is None:
        return None, "missing"
    s = str(raw).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*英寸", s)
    if m:
        return float(m.group(1)), "ok"
    return None, ("missing" if classify_missing(s) else "unparsed")


def parse_refresh_rate(raw):
    """Only explicit single values; never choose a display, range bound or estimate."""
    if raw is None:
        return None, None, "missing"
    s = str(raw).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*Hz\s*(?:ProMotion|[（(]\s*LTPO\s*[）)])?", s, re.I)
    if not m:
        return None, s, ("missing" if classify_missing(s) else "unparsed")
    note = s
    return float(m.group(1)), note, "ok"


def parse_charging(raw):
    """返回 dict(wired_w, wired_approx, wireless_w, wireless_approx, notes)。

    只抽取原文明确写出的功率，不把「约20W」当成精确 20W。
    """
    out = {"wired_w": None, "wired_approx": 0, "wireless_w": None,
           "wireless_approx": 0, "wireless_supported": None, "notes": ""}
    if raw is None:
        return out
    s = str(raw).strip()
    if classify_missing(s):
        return out
    out["notes"] = s
    if "不支持无线充电" in s:
        out["wireless_supported"] = False
    m = re.search(r"(约)?(\d+(?:\.\d+)?)\s*W\s*有线", s)
    if m:
        out["wired_w"] = float(m.group(2))
        out["wired_approx"] = 1 if m.group(1) else 0
    m = re.search(r"(约)?(\d+(?:\.\d+)?)\s*W\s*(?:无线|MagSafe)", s)
    if m:
        out["wireless_w"] = float(m.group(2))
        out["wireless_approx"] = 1 if m.group(1) else 0
    return out


def parse_battery_capacity(raw):
    if raw is None:
        return None, "missing"
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw), "ok"
    s = str(raw).strip()
    reason = classify_missing(s)
    if reason:
        return None, "missing"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)), "ok"
    return None, "unparsed"


# 主表 -> 规格字段映射：(源列, spec_key, 单位, 解析器)
SPEC_MAP = [
    ("重量", "weight_g", "g", parse_weight),
    ("后摄像头", "rear_camera", "-", None),
    ("前摄像头", "front_camera", "-", None),
    ("处理器", "chipset", "-", None),
    ("屏幕大小", "screen_size_inch", "英寸", parse_screen_size),
    ("分辨率", "resolution", "-", None),
    ("屏幕刷新率", "refresh_rate_hz", "Hz", parse_refresh_rate),
    ("屏幕形态", "screen_form", "-", None),
    ("屏幕材质", "screen_material", "-", None),
    ("NFC", "has_nfc", "-", parse_tristate),
    ("红外遥控（发射器）", "has_ir_blaster", "-", parse_tristate),
    ("扬声器配置", "speaker_config", "-", None),
    ("3.5mm耳机孔", "has_headphone_jack", "-", parse_tristate),
    ("电池典型容量(mAh)", "battery_capacity_mah", "mAh", parse_battery_capacity),
    ("续航表现", "battery_life_text", "-", None),
    ("续航测试口径", "battery_life_protocol", "-", None),
    ("无线充电", "has_wireless_charge", "-", parse_tristate),
]

# 屏幕亮度、充电功率单独处理（数值 + 口径/条件），见下方专门分支。


def build_derived(cfg, paths, main_rows, camera_rows, aux_rows, model_by_key,
                  model_id_by_name, collected_at):
    issues = []
    sources = SourceRegistry(collected_at)

    skus, prices, specs = [], [], []
    runs, observations, mappings = [], [], []
    snippets = []
    snip_seen = set()
    game_run_by_model, dxo_run_by_model, other_run_by_model = {}, {}, {}
    spec_seq = 0
    price_seq = 0
    run_seq = 0
    obs_seq = 0

    tb_to_gb = cfg["conventions"]["storage_tb_to_gb"]

    # ---------------- 主表：配置 / 价格 / 规格 ----------------
    spec_seen = set()   # 配置级先完整保留；只有全部配置原文和口径完全一致才提升为机型共用。
    for r in main_rows:
        mid = r["_model_id"]
        vid = r["_variant_id"]
        sid = r["_sku_id"]
        src = r["source_record_id"]

        ram_gb = parse_ram_gb(r.get("运行内存"))
        storage_gb, mem_type, st_status = parse_storage(r.get("存储"), tb_to_gb)
        if st_status == "unparsed":
            issues.append(dict(issue_code="STORAGE_UNPARSED", severity="medium",
                               source_record_id=src,
                               detail=f"存储原文 {r.get('存储')!r} 无法按规则换算，storage_gb 记为 NULL"))
        if ram_gb is None:
            issues.append(dict(issue_code="RAM_UNPARSED", severity="medium",
                               source_record_id=src,
                               detail=f"运行内存原文 {r.get('运行内存')!r} 无法换算，ram_gb 记为 NULL"))

        skus.append({
            "sku_id": sid, "variant_id": vid, "model_id": mid,
            "ram_raw": r.get("运行内存"), "ram_gb": ram_gb,
            "storage_raw": r.get("存储"), "storage_gb": storage_gb,
            "memory_type": mem_type,
            "memory_type_source": "源表存储原文括号标注" if mem_type else "",
            "source_record_id": src,
        })

        # ---- 价格 ----
        price_seq += 1
        pr = parse_price(r.get("价格"))
        note = (r.get("价格说明") or "").strip()
        kind, cond_from_note = PRICE_KIND_MAP.get(note, (None, PRICE_CONDITION_UNKNOWN))
        cond = pr["condition_marker"] or cond_from_note
        if pr["condition_marker"] == "used" and cond_from_note == "new":
            issues.append(dict(issue_code="PRICE_CONDITION_CONFLICT", severity="high",
                               source_record_id=src,
                               detail=f"价格原文含「二手」但价格说明为 {note!r}，已按原文判为 used"))
        if kind is None and note:
            issues.append(dict(issue_code="PRICE_KIND_UNMAPPED", severity="medium",
                               source_record_id=src,
                               detail=f"价格说明 {note!r} 未在映射表中，price_kind 记为 NULL"))
        if kind == "marketplace_listing_price":
            issues.append(dict(issue_code="PRICE_CONDITION_UNVERIFIED", severity="medium",
                               source_record_id=src,
                               detail=f"价格说明 {note!r} 未提供成色/版本证据，condition 记为 unknown，"
                                      f"不静默归入新机或二手；price_cny={pr['amount']}"))
        if pr["status"] == "ambiguous_multi_number":
            issues.append(dict(issue_code="PRICE_RANGE_NOT_SINGLE_VALUE", severity="high",
                               source_record_id=src,
                               detail=f"价格原文 {r.get('价格')!r} 含多个数字，未取首值，price_cny 记为 NULL"))
        if pr["status"] == "missing":
            issues.append(dict(issue_code="PRICE_MISSING", severity="high",
                               source_record_id=src, detail="价格原文为空或为缺失标记"))
        ver_raw = (r.get("版本/备注") or "").strip()
        if "起" in ver_raw:
            issues.append(dict(issue_code="PRICE_QUALIFIER_START_FROM", severity="medium",
                               source_record_id=src,
                               detail=f"版本/备注={ver_raw!r} 表示起售价限定，已保留限定条件；"
                                      f"本行价格 {r.get('价格')!r} 为该配置标价"))
        if "调价" in note:
            issues.append(dict(issue_code="PRICE_TIME_QUALIFIER", severity="info",
                               source_record_id=src,
                               detail=f"价格说明 {note!r} 带调价时间限定，已保留在 price_note"))
        prices.append({
            "price_id": f"P{price_seq:05d}",
            "sku_id": sid,
            "amount": pr["amount"],
            "currency": pr["currency"],
            "price_raw": r.get("价格"),
            "price_kind": kind,
            "condition": cond,
            "is_estimated": 1 if ("约" in note or "approximate" in pr["qualifiers"]) else 0,
            "channel": derive_channel(r.get("价格来源")),
            "seller": r.get("价格来源"),
            "price_note": note,
            "qualifiers": ",".join(pr["qualifiers"]),
            "source_sale_status": r.get("销售状态"),
            "observed_at": r.get("查询日期"),
            "observed_at_kind": "查询日期（源表列名，非成交时间）",
            "source_id": sources.get(r.get("价格来源"), "price"),
            "source_record_id": src,
        })

        # ---- 规格（机型级，去重）----
        for src_col, spec_key, unit, parser in SPEC_MAP:
            if (sid, spec_key) in spec_seen:
                continue
            raw = r.get(src_col)
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                continue
            spec_seen.add((sid, spec_key))
            spec_seq += 1
            num, status, extra = None, "ok", None
            if parser is None:
                num, status = parse_number(raw)
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    num, status = float(raw), "ok"
            else:
                res = parser(raw)
                if isinstance(res, tuple) and len(res) == 3:
                    num, extra, status = res
                else:
                    num, status = res
            if isinstance(num, bool):
                num = 1 if num else 0
            if status == "missing":
                num = None
            if spec_key == 'refresh_rate_hz' and status == 'unparsed':
                issues.append(dict(issue_code='REFRESH_NOT_EXACT_SINGLE_VALUE',severity='medium',
                    source_record_id=src,detail=f'刷新率原文 {raw!r} 无法作为无作用域精确单值；原文保留，数值 NULL，不取首值'))
            specs.append({
                "spec_id": f"SP{spec_seq:05d}",
                "subject_type": "sku", "subject_id": sid, "variant_id": vid,
                "spec_key": spec_key, "unit": unit,
                "value_num": num, "value_text": None if num is not None else str(raw),
                "raw_value": str(raw), "raw_value_type": type(raw).__name__,
                "source_col": src_col, "source_record_id": src,
                "source_id": sources.get(r.get("新增参数来源"), "spec"),
                "parse_status": status, "note": extra or "",
                "rule_version": RULE_VERSION,
            })

        # 屏幕亮度：数值 + 口径（可比性条件）
        if (sid, "display_peak_brightness_nit") not in spec_seen:
            raw = r.get("屏幕亮度(nit)")
            if raw not in (None, ""):
                spec_seen.add((sid, "display_peak_brightness_nit"))
                spec_seq += 1
                num, status = parse_number(raw)
                specs.append({
                    "spec_id": f"SP{spec_seq:05d}",
                    "subject_type": "sku", "subject_id": sid, "variant_id": vid,
                    "spec_key": "display_peak_brightness_nit", "unit": "nit",
                    "value_num": num, "value_text": None if num is not None else str(raw),
                    "raw_value": str(raw), "raw_value_type": type(raw).__name__,
                    "source_col": "屏幕亮度(nit)", "source_record_id": src,
                    "source_id": sources.get(r.get("亮度数据来源"), "brightness"),
                    "parse_status": status, "note": r.get("亮度口径") or "",
                    "rule_version": RULE_VERSION,
                })

        # 充电功率拆解
        if (sid, "wired_charge_w") not in spec_seen:
            ch = parse_charging(r.get("充电功率"))
            if ch["notes"]:
                spec_seen.add((sid, "wired_charge_w"))
                for key, val, unit in (("wired_charge_w", ch["wired_w"], "W"),
                                       ("wireless_charge_w", ch["wireless_w"], "W")):
                    spec_seq += 1
                    source_col, charge_raw = "充电功率", r.get("充电功率")
                    approximate = ch["wired_approx" if key == "wired_charge_w" else "wireless_approx"]
                    charge_status = "approximate" if approximate else ("ok" if val is not None else "unparsed")
                    if approximate:
                        val = None  # 保留近似原文，不将约值冒充精确规格。
                    if key == "wireless_charge_w" and r.get("无线充电最高功率(W)") not in (None, ""):
                        source_col, charge_raw = "无线充电最高功率(W)", r.get("无线充电最高功率(W)")
                        val, charge_status = parse_number(charge_raw)
                    specs.append({
                        "spec_id": f"SP{spec_seq:05d}",
                        "subject_type": "sku", "subject_id": sid, "variant_id": vid,
                        "spec_key": key, "unit": unit,
                        "value_num": val, "value_text": None if val is not None else None,
                        "raw_value": str(charge_raw),
                        "raw_value_type": type(charge_raw).__name__,
                        "source_col": source_col, "source_record_id": src,
                        "source_id": sources.get(r.get("新增参数来源"), "spec"),
                        "parse_status": charge_status,
                        "note": ch["notes"], "rule_version": RULE_VERSION,
                    })

        # 无线充电功率 0 的编码说明
        wc_raw = r.get("无线充电最高功率(W)")
        if r.get("无线充电") == "无" and wc_raw == 0:
            issues.append(dict(issue_code="WIRELESS_POWER_ZERO_ENCODING", severity="info",
                               source_record_id=src,
                               detail="源表在「无线充电=无」的行把功率列写为 0；按源单元格值保留 0，"
                                      "并同时记 has_wireless_charge=false，0 不代表实测功率"))

        # ---- 发布信息 ----
        rel_month, granularity = parse_release_month(r.get("发布时间"))
        if rel_month is None and r.get("发布时间"):
            issues.append(dict(issue_code="RELEASE_MONTH_UNPARSED", severity="medium",
                               source_record_id=src,
                               detail=f"发布时间 {r.get('发布时间')!r} 无法解析到月粒度"))
        r["_release_month"] = rel_month
        r["_release_granularity"] = granularity

    # ---------------- 游戏评测运行（机型级归并）----------------
    run_index = {}
    for r in main_rows:
        mid = r["_model_id"]
        cond_raw = (r.get("王者测试条件") or "").strip()
        src_raw = (r.get("游戏数据来源") or "").strip()
        # Preserve differing source measurements as distinct runs. Same conditions do
        # not prove a repeated observation is the same test or permit taking row one.
        measurement_signature = tuple(str(r.get(col)) for col in
            ('王者平均帧率(FPS)', '王者平均功耗(W)', '王者1%Low帧(FPS)', '王者机身最高温度(℃)'))
        key = (mid, cond_raw, src_raw, measurement_signature)
        if key not in run_index:
            run_seq += 1
            run_id = f"RUN{run_seq:05d}"
            run_index[key] = run_id
            conditions, cond_extra = parse_game_conditions(cond_raw)
            observations_pending = []
            for col, metric_key, unit in (
                ("王者平均帧率(FPS)", "honor_of_kings_avg_fps", "FPS"),
                ("王者平均功耗(W)", "honor_of_kings_power_w", "W"),
                ("王者1%Low帧(FPS)", "honor_of_kings_1pct_low_fps", "FPS"),
                ("王者机身最高温度(℃)", "honor_of_kings_max_temp_c", "℃"),
            ):
                observations_pending.append((col, metric_key, unit, r.get(col)))
            runs.append({
                "run_id": run_id, "subject_type": "model", "subject_id": mid,
                "model_id": mid, "variant_id": None, "sku_id": None,
                "benchmark": "王者荣耀", "institution": derive_institution(src_raw),
                "protocol": "王者荣耀帧率/功耗/温度实测",
                "protocol_detail": cond_raw,
                "conditions_json": json.dumps(conditions, ensure_ascii=False),
                "run_date": conditions.get("run_period", ""),
                "os_version": conditions.get("os", ""),
                "comparison_group": comparison_group("honor_of_kings_avg_fps", None, cond_raw),
                "applicable_scope": "model",
                "scope_note": "源表注明同机型各配置共用参考，非逐配置实测",
                "source_id": sources.get(src_raw, "game"),
                "source_record_id": r["source_record_id"],
                "data_note": r.get("数据备注"),
                "parse_note": cond_extra,
            })
            for col, metric_key, unit, raw in observations_pending:
                obs_seq += 1
                observations.append(make_observation(
                    f"OB{obs_seq:05d}", run_id, metric_key, unit, raw, col,
                    r["source_record_id"], condition=cond_raw))
                if classify_missing(raw) == MISSING_NOT_REPORTED:
                    issues.append(dict(issue_code="METRIC_NOT_REPORTED", severity="info",
                                       source_record_id=r["source_record_id"],
                                       detail=f"{col} 来源未公开，数值 NULL，原因 not_reported"))
                if raw is not None and parse_number(raw)[1] == "non_numeric":
                    issues.append(dict(issue_code="MEASUREMENT_NON_NUMERIC", severity="medium",
                                       source_record_id=r["source_record_id"],
                                       detail=f"{col} 原文 {raw!r} 为不等号/区间表达，"
                                              f"未取近似值，数值 NULL"))
        r["_game_run_id"] = run_index[key]
        game_run_by_model.setdefault(mid, run_index[key])

    # ---------------- 相机评测运行 ----------------
    for r in camera_rows:
        name = r.get("产品名称")
        mid = model_id_by_name.get(normalize_match_key(name))
        if mid is None:
            issues.append(dict(issue_code="MODEL_UNMATCHED", severity="high",
                               source_record_id=r["source_record_id"],
                               detail=f"拍照表机型 {name!r} 在主表中找不到对应 model_id"))
            continue
        r["_model_id"] = mid
        agency = (r.get("主评分机构") or "").strip()
        overall_raw = r.get("DXOMARK总分")
        has_dxo = overall_raw not in (None, "")
        other = (r.get("其他机构评分") or "").strip()

        # ---- DXOMARK 运行：仅在确有 DXOMARK 数据时创建 ----
        if has_dxo or agency == "DXOMARK":
            run_seq += 1
            run_id = f"RUN{run_seq:05d}"
            protocol = (r.get("DXOMARK协议版本") or "").strip() or None
            if protocol and "另有V6复测" in protocol:
                issues.append(dict(issue_code="DXOMARK_MULTI_PROTOCOL", severity="medium",
                                   source_record_id=r["source_record_id"],
                                   detail=f"协议版本 {protocol!r} 表示同一机型存在 V5 与 V6 两次协议结果，"
                                          f"本行按源表所填协议归组，未做跨协议合并"))
            if protocol in ("v4", "v5", "v6"):
                issues.append(dict(issue_code="DXOMARK_PROTOCOL_DIFFERENCE", severity="info",
                                   source_record_id=r["source_record_id"],
                                   detail=f"DXOMARK {protocol} 协议，与其他协议版本不可直接混排比较"))
            runs.append({
                "run_id": run_id, "subject_type": "model", "subject_id": mid,
                "model_id": mid, "variant_id": None, "sku_id": None,
                "benchmark": "相机综合", "institution": "DXOMARK",
                "protocol": f"DXOMARK {protocol}" if protocol else "DXOMARK（协议版本源表未标注）",
                "protocol_detail": (r.get("备注") or ""),
                "conditions_json": json.dumps({"dxomark_protocol": protocol}, ensure_ascii=False),
                "run_date": extract_source_date(r.get("数据来源"))[0] or "",
                "os_version": "",
                "comparison_group": comparison_group("dxomark_overall", protocol),
                "applicable_scope": "model",
                "scope_note": "源表仅标注机型，未区分购买配置",
                "source_id": sources.get(r.get("数据来源"), "camera"),
                "source_record_id": r["source_record_id"],
                "data_note": r.get("备注"),
                "parse_note": "",
            })
            dxo_run_by_model[mid] = run_id
            for col, metric_key, unit in (
                ("DXOMARK总分", "dxomark_overall", "分"),
                ("DXOMARK拍照分", "dxomark_photo", "分"),
                ("DXOMARK视频分", "dxomark_video", "分"),
                ("DXOMARK变焦分", "dxomark_zoom", "分"),
                ("DXOMARK夜景分", "dxomark_lowlight", "分"),
            ):
                raw = r.get(col)
                if raw in (None, "") and col == "DXOMARK变焦分":
                    issues.append(dict(issue_code="DXOMARK_SUBSCORE_ABSENT", severity="info",
                                       source_record_id=r["source_record_id"],
                                       detail="变焦(Zoom/Tele)子分源页面未单列，数值 NULL，不推算"))
                if raw in (None, "") and col == "DXOMARK夜景分":
                    issues.append(dict(issue_code="DXOMARK_SUBSCORE_ABSENT", severity="info",
                                       source_record_id=r["source_record_id"],
                                       detail="夜景(Lowlight)子分源页面未单列，数值 NULL，不推算"))
                obs_seq += 1
                observations.append(make_observation(
                    f"OB{obs_seq:05d}", run_id, metric_key, unit, raw, col,
                    r["source_record_id"], protocol=protocol))
            norm_raw = r.get("归一化分值（百分制）")
            if norm_raw not in (None, ""):
                obs_seq += 1
                observations.append(make_observation(
                    f"OB{obs_seq:05d}", run_id, "dxomark_normalized_100", "分", norm_raw,
                    "归一化分值（百分制）", r["source_record_id"], protocol=protocol,
                    group="source_normalized_not_comparable_across_protocols",
                    extra_note=r.get("归一化说明")))
                issues.append(dict(issue_code="NORMALIZED_SCORE_SOURCE_PROVIDED", severity="info",
                                   source_record_id=r["source_record_id"],
                                   detail="归一化分值为源表已有字段，按源数据导入；"
                                          "默认不作为跨协议统一排名指标，本次不拟合跨来源校准模型"))
        elif overall_raw in (None, "") and agency and agency != "无（硬件参数补充）":
            issues.append(dict(issue_code="CAMERA_AGENCY_NO_SCORE", severity="medium",
                               source_record_id=r["source_record_id"],
                               detail=f"主评分机构为 {agency} 但总分列为空，未创建评测运行"))

        # ---- 其他机构评分：独立运行，不挂在 DXOMARK 运行下 ----
        if other:
            m = re.search(r"总分\s*(\d+(?:\.\d+)?)", other)
            m2 = re.search(r"照片\s*(\d+(?:\.\d+)?)", other)
            if m or m2:
                run_seq += 1
                o_run = f"RUN{run_seq:05d}"
                o_inst = "PhoneArena" if "PhoneArena" in other else "其他机构"
                runs.append({
                    "run_id": o_run, "subject_type": "model", "subject_id": mid,
                    "model_id": mid, "variant_id": None, "sku_id": None,
                    "benchmark": "相机综合", "institution": o_inst,
                    "protocol": f"{o_inst} 相机基准",
                    "protocol_detail": other,
                    "conditions_json": json.dumps({"agency": o_inst}, ensure_ascii=False),
                    "run_date": extract_source_date(other)[0] or "",
                    "os_version": "",
                    "comparison_group": comparison_group("phonearena_camera_overall", None),
                    "applicable_scope": "model",
                    "scope_note": "源表「其他机构评分」列，与 DXOMARK 分属不同机构，不混排",
                    "source_id": sources.get(other, "camera_other"),
                    "source_record_id": r["source_record_id"],
                    "data_note": other, "parse_note": "",
                })
                other_run_by_model[mid] = o_run
                if m:
                    obs_seq += 1
                    observations.append(make_observation(
                        f"OB{obs_seq:05d}", o_run, "phonearena_camera_overall", "分", m.group(1),
                        "其他机构评分", r["source_record_id"], group="phonearena",
                        extra_note=other))
                if m2:
                    obs_seq += 1
                    observations.append(make_observation(
                        f"OB{obs_seq:05d}", o_run, "phonearena_camera_photo", "分", m2.group(1),
                        "其他机构评分", r["source_record_id"], group="phonearena",
                        extra_note=other))
            else:
                issues.append(dict(issue_code="OTHER_AGENCY_SCORE_UNPARSED", severity="medium",
                                   source_record_id=r["source_record_id"],
                                   detail=f"其他机构评分文字 {other[:60]!r} 无固定分数格式，"
                                          f"未生成指标观测，仅保留来源文字"))
        spec_seq = add_camera_specs(specs, spec_seq, mid, r, sources, issues)

    # ---------------- 评测摘录与检索标签 ----------------
    def add_snippet(mid, kind, text, source_col, source_record_id, source_id,
                    run_id=None, metric_keys=None, origin="source_table_human",
                    extra_note="", source_ref_col=""):
        """原文摘录 + 自动检索标签。原文逐字保留，标签单独标记提取方式。

        source_col      产生这段文字的正文列（如「续航测试口径」）
        source_ref_col  驱动 source_id 的来源列（如「续航来源」）；
                        仅当该列在原始层为空/占位符时 source_id 才允许为 NULL。
        """
        text = (text or "").strip()
        if not text or mid is None:
            return
        key = (mid, kind, text)
        if key in snip_seen:
            return
        snip_seen.add(key)
        tags = extract_tags(text)
        snippets.append({
            "snippet_id": f"SN{len(snippets) + 1:05d}",
            "source_id": source_id,
            "subject_type": "model", "subject_id": mid,
            "related_run_id": run_id,
            "related_metric_keys": ",".join(metric_keys or SNIPPET_KIND_METRICS.get(kind, [])),
            "snippet_kind": kind,
            "snippet_text": text,
            "source_col": source_col,
            "source_ref_col": source_ref_col,
            "source_record_id": source_record_id,
            "origin": origin,
            "extraction_method": "source_column",
            "tag_method": TAG_RULE_VERSION,
            "tags": ",".join(tags),
            "tag_count": len(tags),
            "note": extra_note,
        })

    # 相机表：结论 / 硬件补充 / 备注 / 夜景备注 / 其他机构评分
    for r in camera_rows:
        mid = r.get("_model_id")
        if not mid:
            continue
        src = r["source_record_id"]
        cam_src = sources.get(r.get("数据来源"), "camera")
        add_snippet(mid, "review_conclusion", r.get("专业评测结论要点"),
                    "专业评测结论要点", src, cam_src,
                    run_id=dxo_run_by_model.get(mid),
                    extra_note="源表人工撰写结论原文，未转成 0-10 评分",
                    source_ref_col="数据来源")
        add_snippet(mid, "hardware_note", r.get("摄像头硬件参数补充"),
                    "摄像头硬件参数补充", src, cam_src,
                    extra_note="相机硬件补充文字，未折算为评分",
                    source_ref_col="数据来源")
        add_snippet(mid, "camera_note", r.get("备注"), "备注", src, cam_src,
                    run_id=dxo_run_by_model.get(mid),
                    source_ref_col="数据来源")
        add_snippet(mid, "lowlight_note", r.get("DXOMARK夜景备注"),
                    "DXOMARK夜景备注", src, cam_src,
                    run_id=dxo_run_by_model.get(mid),
                    source_ref_col="数据来源")
        add_snippet(mid, "other_agency_score", r.get("其他机构评分"),
                    "其他机构评分", src, cam_src,
                    run_id=other_run_by_model.get(mid),
                    extra_note="其他机构评分原文，与 DXOMARK 分属不同机构",
                    source_ref_col="数据来源")

    # 主表：游戏数据备注 / 新增配置备注 / 续航与亮度口径
    for r in main_rows:
        mid = r["_model_id"]
        src = r["source_record_id"]
        add_snippet(mid, "game_data_note", r.get("数据备注"), "数据备注", src,
                    sources.get(r.get("游戏数据来源"), "game"),
                    run_id=game_run_by_model.get(mid),
                    extra_note="含「同机型各配置共用参考」「未按芯片推算」等限定说明",
                    source_ref_col="游戏数据来源")
        add_snippet(mid, "config_note", r.get("新增配置备注"), "新增配置备注", src,
                    sources.get(r.get("新增参数来源"), "spec"),
                    source_ref_col="新增参数来源")
        add_snippet(mid, "battery_protocol", r.get("续航测试口径"), "续航测试口径", src,
                    sources.get(r.get("续航来源"), "spec"),
                    source_ref_col="续航来源")
        add_snippet(mid, "brightness_protocol", r.get("亮度口径"), "亮度口径", src,
                    sources.get(r.get("亮度数据来源"), "brightness"),
                    metric_keys=["display_peak_brightness_nit"],
                    extra_note="亮度口径决定可比性，不同口径不直接混排",
                    source_ref_col="亮度数据来源")

    # 摘录来源缺失登记：来源列本身为空/占位符时才允许 source_id 为空
    no_src = {}
    for s in snippets:
        if not s["source_id"]:
            col = s.get("source_ref_col") or s["source_col"]
            no_src[col] = no_src.get(col, 0) + 1
    for col, n in sorted(no_src.items()):
        issues.append(dict(issue_code="SNIPPET_SOURCE_UNSPECIFIED", severity="info",
                           source_record_id="",
                           detail=f"「{col}」列在源表部分行为空，对应 {n} 条摘录无来源记录，"
                                  f"source_id 记为 NULL；未伪造链接。"
                                  f"（已在 evidence_snippets.source_ref_col 标注来源列，"
                                  f"可逐条回查原始层该单元格确认确实为空）"))

    # 规格层可比性分组：统一在此补全，保证所有来源的规格行都有该字段。
    # 亮度按规范化口径类别分组（原文仍存 note）；其余规格同一 spec_key 内同量纲直接可比。
    for s in specs:
        s["comparison_group"] = spec_comparison_group(s["spec_key"], s.get("note") or "")

    # 不再取同机型首行：全量配置都有该字段、原文/类型/数值/来源/口径一致，才可共用。
    sku_model = {s['sku_id']: s['model_id'] for s in skus}
    model_skus = {}
    grouped_specs = {}
    for sku in skus:
        model_skus.setdefault(sku['model_id'], set()).add(sku['sku_id'])
    for spec in specs:
        if spec['subject_type'] == 'sku':
            grouped_specs.setdefault((sku_model[spec['subject_id']], spec['spec_key']), []).append(spec)
    removed = set()
    spec_provenance = [{key: s.get(key) for key in ('spec_id', 'source_record_id', 'source_col', 'source_id')}
                       for s in specs]
    merged_into = {}
    signature_keys = ('unit', 'value_num', 'value_text', 'raw_value', 'raw_value_type',
                      'source_col', 'source_id', 'parse_status', 'note', 'comparison_group')
    for (mid, key), records in grouped_specs.items():
        signatures = {tuple(s.get(k) for k in signature_keys) for s in records}
        if {s['subject_id'] for s in records} == model_skus[mid] and len(signatures) == 1:
            first = records[0]
            first['subject_type'], first['subject_id'], first['variant_id'] = 'model', mid, None
            removed.update(s['spec_id'] for s in records[1:])
            merged_into.update({s['spec_id']: first['spec_id'] for s in records[1:]})
        elif len(signatures) > 1:
            issues.append(dict(issue_code='SKU_SPEC_SCOPE_DIFFERENCE', severity='info',
                               source_record_id=records[0]['source_record_id'],
                               detail=f'{mid} 的 {key} 在配置间有差异，保留 {len(records)} 条配置级规格，不自动共用'))
    specs = [s for s in specs if s['spec_id'] not in removed]
    for link in spec_provenance:
        link['spec_id'] = merged_into.get(link['spec_id'], link['spec_id'])

    return {
        "skus": skus, "price_snapshots": prices, "specifications": specs,
        "benchmark_runs": runs, "metric_observations": observations,
        "sources": sources.rows, "evidence_snippets": snippets,
        'spec_provenance': spec_provenance,
    }, issues


# ---------------------------------------------------------------- 辅助

def derive_channel(raw):
    if not raw:
        return ""
    s = str(raw)
    if s.startswith("淘宝"):
        return "淘宝"
    if "官网" in s:
        return "品牌官网"
    if "二手平台" in s:
        return "二手平台行情综合"
    if "GSMArena" in s:
        return "GSMArena"
    if "DXOMARK" in s:
        return "DXOMARK"
    return ""


def derive_institution(raw):
    if not raw or raw in ("—", "-"):
        return ""
    s = str(raw)
    m = URL_RE.search(s)
    if m:
        host = m.group(0).split("//", 1)[-1].split("/")[0]
        return host
    return s[:60]


def parse_game_conditions(raw):
    """把「60帧+极致；10V10；25±0.5℃；350nit；iOS18.0」拆成结构化条件。"""
    cond = {}
    extra = ""
    if not raw:
        return cond, extra
    s = str(raw).strip()
    if classify_missing(s):
        cond["missing_reason"] = MISSING_NOT_FOUND
        return cond, "源表标记为未查到公开实测条件"
    parts = re.split(r"[；;]", s)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if re.search(r"\d+\s*帧", p):
            cond["frame_mode"] = p
            match = re.fullmatch(r'(\d+)\s*帧(?:\+[^；;]+)?', p)
            if match:
                cond['fps_target'] = int(match.group(1))
        elif re.fullmatch(r'渲染分辨率\s*[=:：]\s*\d+\s*[xX×]\s*\d+', p):
            cond['render_resolution'] = re.split(r'[=:：]', p, maxsplit=1)[1].strip().replace('×','x').replace('X','x')
        elif "画质" in p or p in ("极致", "高清", "均衡", "流畅"):
            cond["quality"] = p
        elif "℃" in p:
            cond["ambient_temp"] = p
        elif "nit" in p.lower():
            cond["screen_brightness"] = p
        elif re.search(r"iOS|Android|鸿蒙|HarmonyOS|MagicOS|HyperOS|ColorOS|OriginOS|MIUI|One UI", p, re.I):
            cond["os"] = p
            cond['os_version'] = p
        elif re.match(r"^\d{4}-\d{2}", p):
            cond["run_period"] = p.replace("评测", "")
        elif re.match(r"^\d+V\d+$", p):
            cond["match_mode"] = p
        else:
            cond.setdefault("other", []).append(p)
    if "other" in cond:
        cond["other"] = "；".join(cond["other"])
    if not cond:
        extra = "测试条件文字无法结构化拆分，原文已保留"
    return cond, extra


def make_observation(obs_id, run_id, metric_key, unit, raw, source_col,
                     source_record_id, protocol=None, condition=None, group=None,
                     extra_note=None):
    """构造一条指标观测。

    protocol 用于相机等跨协议来源；condition 用于游戏/亮度等条件型口径。
    二者分别决定 comparison_group，避免把「协议」与「测试条件」混为一谈。
    """
    num, status = parse_number(raw)
    missing_reason = None
    if status == "missing":
        missing_reason = classify_missing(raw) or MISSING_NOT_FOUND
        num = None
    elif status == "non_numeric":
        missing_reason = "non_numeric_expression"
        num = None
    if group is None:
        if metric_key.startswith("honor_of_kings_") or metric_key == "display_peak_brightness_nit":
            group = comparison_group(metric_key, None, condition)
        else:
            group = comparison_group(metric_key, protocol)
    return {
        "observation_id": obs_id, "run_id": run_id, "metric_key": metric_key,
        "value_num": num, "unit": unit,
        "raw_value": "" if raw is None else str(raw),
        "raw_value_type": type(raw).__name__ if raw is not None else "empty",
        "missing_reason": missing_reason,
        "source_col": source_col, "source_record_id": source_record_id,
        "comparison_group": group,
        "verification_status": "machine_checked" if num is not None else "raw_only",
        "note": extra_note or "",
    }


def add_camera_specs(specs, spec_seq, mid, r, sources, issues):
    """相机硬件参数补充 -> 规格记录。"""
    hw = (r.get("摄像头硬件参数补充") or "").strip()
    if hw:
        spec_seq += 1
        specs.append({
            "spec_id": f"SP{spec_seq:05d}", "subject_type": "model", "subject_id": mid,
            "variant_id": None, "spec_key": "camera_hardware_note", "unit": "-",
            "value_num": None, "value_text": hw, "raw_value": hw,
            "raw_value_type": "str", "source_col": "摄像头硬件参数补充",
            "source_record_id": r["source_record_id"],
            "source_id": sources.get(r.get("数据来源"), "camera"),
            "parse_status": "text_only", "note": "相机硬件补充文字，未折算为评分",
            "rule_version": RULE_VERSION,
        })
    concl = (r.get("专业评测结论要点") or "").strip()
    if concl:
        spec_seq += 1
        specs.append({
            "spec_id": f"SP{spec_seq:05d}", "subject_type": "model", "subject_id": mid,
            "variant_id": None, "spec_key": "review_conclusion_text", "unit": "-",
            "value_num": None, "value_text": concl, "raw_value": concl,
            "raw_value_type": "str", "source_col": "专业评测结论要点",
            "source_record_id": r["source_record_id"],
            "source_id": sources.get(r.get("数据来源"), "camera"),
            "parse_status": "text_only",
            "note": "专业评测结论原文；本任务不将其转成 0-10 评分", "rule_version": RULE_VERSION,
        })
    return spec_seq
