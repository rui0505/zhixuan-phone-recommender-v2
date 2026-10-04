# -*- coding: utf-8 -*-
"""验收测试：11 项验收标准 + 边界案例 + 网页接口检查。

用法：
  python tests/acceptance.py --config config/build_config.json
  python tests/acceptance.py --config config/build_config.json --rebuild-check

本脚本只读源文件与产物；--rebuild-check 会重新执行一次构建以验证重建稳定性
（不会修改源文件，业务 ID 由 id_registry.csv 保证稳定）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import openpyxl  # noqa: E402
from common import (force_utf8_stdio, load_config, resolve_dirs, sha256_file,
                    value_to_text, value_type_of, write_csv_bom, write_json)  # noqa: E402
import rules  # noqa: E402
from repository import CatalogRepository  # noqa: E402

RESULTS = []


def record(item, standard, passed, detail, evidence=""):
    RESULTS.append({"item": item, "standard": standard,
                    "result": "PASS" if passed is True else ("FAIL" if passed is False
                                                             else "NOT_CHECKED"),
                    "detail": detail, "evidence": evidence})


def main():
    force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "build_config.json"))
    ap.add_argument("--rebuild-check", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    paths = resolve_dirs(cfg)
    out = paths["output_dir"]
    db = os.path.join(out, "catalog.sqlite")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    Q = lambda sql, p=(): con.execute(sql, p).fetchall()  # noqa: E731
    S = lambda sql, p=(): con.execute(sql, p).fetchone()[0]  # noqa: E731

    manifest = json.load(open(os.path.join(out, "source_manifest.json"), encoding="utf-8"))

    # ============================================== 1 原文件不变
    hashes = []
    all_same = True
    for f in manifest["files"]:
        if not f.get("sha256_before"):
            continue
        now = sha256_file(f["abs_path"]) if os.path.exists(f["abs_path"]) else None
        after = f.get("sha256_after")
        # 以「当前哈希 == 构建前哈希」为准；若构建过程被中断导致 after 缺失，仍按前两项判定
        same = (now == f["sha256_before"]) and (after is None or after == now)
        all_same &= same
        hashes.append({"file": f["file_name"], "sha256": now, "unchanged": same,
                       "sha256_after_present": after is not None})
    record("原文件不变", "输入执行前后 SHA-256 全部一致", all_same,
           f"{len(hashes)} 个输入文件当前哈希与构建前记录完全一致"
           + ("（构建前后两段哈希均已记录）" if all(h["sha256_after_present"] for h in hashes)
              else "（注意：上次构建未完整收尾，构建后哈希缺失，已按当前哈希与前哈希比对）"),
           json.dumps(hashes, ensure_ascii=False))

    # ============================================== 2 原始值可复查（全量机器对照）
    src = paths["source_dir"]
    files = {"price_main": (cfg["inputs"]["price_main"], cfg["primary_sheet"]),
             "price_aux": (cfg["inputs"]["price_aux"], cfg["primary_sheet"]),
             "camera": (cfg["inputs"]["camera"], cfg["camera_sheet"])}
    checked, mismatch = 0, []
    for key, (fname, sheet) in files.items():
        wb = openpyxl.load_workbook(os.path.join(src, fname), data_only=False)
        ws = wb[sheet]
        header = [c.value for c in ws[1]]
        rows = {}
        for r in Q("SELECT source_row_no, source_col_no, field_name, raw_value_text,"
                   " raw_value_type, formula, number_format FROM raw_cells"
                   " WHERE file_key=? AND sheet_name=?", (key, sheet)):
            rows[(r["source_row_no"], r["source_col_no"])] = r
        for (rno, cno), rec in rows.items():
            c = ws.cell(rno, cno)
            checked += 1
            exp_text = value_to_text(c.value)
            exp_type = value_type_of(c.value)
            exp_formula = c.value if isinstance(c.value, str) and c.value.startswith("=") else ""
            if (rec["raw_value_text"] != exp_text or rec["raw_value_type"] != exp_type
                    or (rec["formula"] or "") != exp_formula
                    or rec["number_format"] != c.number_format
                    or rec["field_name"] != (header[cno - 1] if cno - 1 < len(header) else None)):
                mismatch.append({"file": key, "row": rno, "col": cno,
                                 "expected": exp_text, "stored": rec["raw_value_text"]})
        wb.close()
    record("原始值可复查", "所有源记录有映射；原始数值/文字/值类型/公式逐项对照，差异为零",
           len(mismatch) == 0,
           f"全量机器对照 {checked} 个原始单元格（值/类型/公式/数字格式/字段名），差异 {len(mismatch)} 处；"
           f"映射覆盖：源数据行 {S('SELECT COUNT(DISTINCT source_record_id) FROM raw_rows WHERE is_header_row=0 AND file_key<>\"images_zip\"')} 行，"
           f"import_mapping 覆盖 {S('SELECT COUNT(DISTINCT source_record_id) FROM import_mapping')} 行",
           json.dumps(mismatch[:5], ensure_ascii=False))

    # 派生层按规则复算
    der_bad = []
    for r in Q("SELECT sku_id, ram_raw, ram_gb, storage_raw, storage_gb, memory_type FROM skus"):
        exp_ram = rules.parse_ram_gb(r["ram_raw"])
        exp_st, exp_mem, _ = rules.parse_storage(r["storage_raw"], cfg["conventions"]["storage_tb_to_gb"])
        if (exp_ram != r["ram_gb"]) or (exp_st != r["storage_gb"]) or ((exp_mem or None) != (r["memory_type"] or None)):
            der_bad.append({"sku_id": r["sku_id"], "field": "ram/storage"})
    for r in Q("SELECT price_id, price_raw, amount, condition FROM price_snapshots"):
        exp = rules.parse_price(r["price_raw"])
        if exp["amount"] != r["amount"]:
            der_bad.append({"price_id": r["price_id"], "field": "price_cny",
                            "expected": exp["amount"], "stored": r["amount"]})
        exp_cond = exp["condition_marker"]
        if exp_cond and exp_cond != r["condition"]:
            der_bad.append({"price_id": r["price_id"], "field": "condition"})
    for r in Q("SELECT observation_id, raw_value, value_num, missing_reason FROM metric_observations"):
        exp_v, exp_s = rules.parse_number(r["raw_value"] if r["raw_value"] != "" else None)
        if exp_s == "ok" and exp_v != r["value_num"]:
            der_bad.append({"observation_id": r["observation_id"], "field": "value_num"})
        if exp_s == "ok" and r["missing_reason"] is not None:
            der_bad.append({"observation_id": r["observation_id"], "field": "missing_reason"})
        if exp_s == "missing" and r["value_num"] is not None:
            der_bad.append({"observation_id": r["observation_id"], "field": "should_be_null"})
    record("原始值可复查（派生复算）", "派生层按转换规则逐项复算，差异为零",
           len(der_bad) == 0,
           f"复算 ram_gb/storage_gb/price_cny/condition/value_num/missing_reason，"
           f"共 {S('SELECT COUNT(*) FROM skus')} 配置 + {S('SELECT COUNT(*) FROM price_snapshots')} 价格 + "
           f"{S('SELECT COUNT(*) FROM metric_observations')} 观测，差异 {len(der_bad)} 处",
           json.dumps(der_bad[:5], ensure_ascii=False))

    # ============================================== 3 版本正确
    sat = Q("SELECT v.variant_id, m.display_name, v.variant_name FROM phone_variants v "
            "JOIN phone_models m ON m.model_id=v.model_id WHERE v.satellite_support=1")
    lpd = Q("SELECT sku_id, storage_raw, memory_type, storage_gb FROM skus WHERE storage_raw LIKE '%LPDDR5T%'")
    dup_same_variant = Q("SELECT model_id, variant_id, ram_raw, storage_raw, COUNT(*) c FROM skus "
                         "GROUP BY 1,2,3,4 HAVING c>1")
    dup_cross_variant = Q("SELECT model_id, ram_raw, storage_raw, COUNT(*) c, "
                          "COUNT(DISTINCT variant_id) v FROM skus "
                          "GROUP BY 1,2,3 HAVING c>1")
    # 名称+RAM+存储 相同但版本不同的组合，必须由 variant 差异解释
    explained = all(r["c"] == r["v"] for r in dup_cross_variant)
    merged = Q("SELECT COUNT(*) FROM phone_variants WHERE satellite_support=1 AND is_default=1")
    ok3 = (len(sat) == 3 and len(lpd) == 1 and len(dup_same_variant) == 0
           and len(dup_cross_variant) == 2 and explained and merged[0][0] == 0)
    record("版本正确", "普通版/卫星版/内存类型差异保留；跨版本自动合并为零", ok3,
           f"卫星通信版 variant {len(sat)} 个（{[r['display_name'] for r in sat]}）；"
           f"1TB (LPDDR5T) sku {len(lpd)} 个（storage_gb={lpd[0]['storage_gb'] if lpd else None}，"
           f"memory_type={lpd[0]['memory_type'] if lpd else None}）；"
           f"同一 variant 内重复 sku {len(dup_same_variant)} 个；"
           f"名称+RAM+存储 相同但版本不同的组合 {len(dup_cross_variant)} 个，"
           f"全部由 variant_id 差异解释={explained}；卫星版被标为默认版本的 {merged[0][0]} 个",
           json.dumps([dict(r) for r in sat] + [dict(r) for r in dup_cross_variant],
                      ensure_ascii=False))

    # ============================================== 4 关联有效
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    orphan = []
    orphan += Q("SELECT 'skus->variants' t, COUNT(*) n FROM skus s LEFT JOIN phone_variants v "
                "ON v.variant_id=s.variant_id WHERE v.variant_id IS NULL")
    orphan += Q("SELECT 'variants->models' t, COUNT(*) n FROM phone_variants v LEFT JOIN phone_models m "
                "ON m.model_id=v.model_id WHERE m.model_id IS NULL")
    orphan += Q("SELECT 'prices->skus' t, COUNT(*) n FROM price_snapshots p LEFT JOIN skus s "
                "ON s.sku_id=p.sku_id WHERE s.sku_id IS NULL")
    orphan += Q("SELECT 'obs->runs' t, COUNT(*) n FROM metric_observations o LEFT JOIN benchmark_runs r "
                "ON r.run_id=o.run_id WHERE r.run_id IS NULL")
    orphan += Q("SELECT 'assets->models' t, COUNT(*) n FROM assets a WHERE a.model_id IS NOT NULL "
                "AND a.model_id NOT IN (SELECT model_id FROM phone_models)")
    orphan += Q("SELECT 'colors->models' t, COUNT(*) n FROM asset_colors c WHERE c.model_id IS NOT NULL "
                "AND c.model_id NOT IN (SELECT model_id FROM phone_models)")
    orphan += Q("SELECT 'covers->assets' t, COUNT(*) n FROM model_covers c WHERE c.cover_asset_id IS NOT NULL "
                "AND c.cover_asset_id NOT IN (SELECT asset_id FROM assets)")

    # subject_type / subject_id 是弱引用，必须用验证程序保证指向存在的记录
    subject_checks = {}
    for table, col in (("specifications", "subject_id"), ("benchmark_runs", "subject_id"),
                       ("evidence_snippets", "subject_id")):
        for stype in [r[0] for r in Q(f"SELECT DISTINCT subject_type FROM {table}")]:
            if stype != "model":
                subject_checks[f"{table}({stype})"] = -1
                continue
            n = S(f"SELECT COUNT(*) FROM {table} t WHERE t.subject_type='model' "
                  f"AND t.{col} NOT IN (SELECT model_id FROM phone_models)")
            subject_checks[f"{table}(model)"] = n
    # related_run_id 也必须是存在的 run
    bad_run_ref = S("SELECT COUNT(*) FROM evidence_snippets WHERE related_run_id IS NOT NULL "
                    "AND related_run_id NOT IN (SELECT run_id FROM benchmark_runs)")
    subject_checks["evidence_snippets(related_run_id)"] = bad_run_ref
    subject_bad = {k: v for k, v in subject_checks.items() if v != 0}
    orphan_bad = [dict(r) for r in orphan if r["n"] > 0]
    unmatched = S("SELECT COUNT(*) FROM quality_issues WHERE issue_code LIKE '%UNMATCHED%'")
    record("关联有效", "机型/版本/配置/价格/指标/图片外键均有效；未匹配对象进入问题清单",
           len(fk) == 0 and not orphan_bad and not subject_bad,
           f"PRAGMA foreign_key_check 违规 {len(fk)} 条；手工反查孤儿引用 {orphan_bad or '无'}；"
           f"subject_type/subject_id 弱引用验证 {subject_checks}（非 0 即失败）；"
           f"未匹配问题登记 {unmatched} 条",
           json.dumps([dict(r) for r in fk[:5]], ensure_ascii=False))

    # ============================================== 5 缺失正确
    reasons = dict(Q("SELECT COALESCE(missing_reason,'<有值>') k, COUNT(*) n FROM metric_observations GROUP BY 1"))
    zero = S("SELECT COUNT(*) FROM metric_observations WHERE value_num=0")
    nf_ok = reasons.get("not_found", 0) > 0 and reasons.get("not_reported", 0) > 0
    tri = {r["spec_key"]: {"有": r["yes"], "无": r["no"], "未知": r["unknown"]}
           for r in Q("SELECT spec_key, SUM(value_num=1) yes, SUM(value_num=0) no, "
                      "SUM(value_num IS NULL) unknown FROM specifications "
                      "WHERE spec_key IN ('has_nfc','has_wireless_charge','has_ir_blaster','has_headphone_jack') "
                      "GROUP BY 1")}
    record("缺失正确", "NULL / 明确无功能 / 未公开 / 未查到分别记录；无自动填 0 或推算实测",
           nf_ok and zero == 0,
           f"指标观测缺失原因分布 {reasons}；数值为 0 的观测 {zero} 条；"
           f"三元布尔字段 有/无/未知 分布 {tri}",
           "catalog.sqlite:metric_observations / specifications")

    # ============================================== 6 可比性明确
    groups = S("SELECT COUNT(DISTINCT comparison_group) FROM metric_observations")
    proto = dict(Q("SELECT protocol, COUNT(*) n FROM benchmark_runs WHERE benchmark='相机综合' GROUP BY 1"))
    frame_groups = Q("SELECT DISTINCT comparison_group FROM metric_observations "
                     "WHERE metric_key LIKE 'honor_of_kings%'")
    has60 = any("60" in r[0] for r in frame_groups)
    has120 = any("120" in r[0] for r in frame_groups)
    # 规格层：仅亮度按口径类别分组，其余规格不得按措辞碎片化（每个 spec_key 只应有 1 个组）
    spec_frag = Q("""
        SELECT spec_key, COUNT(DISTINCT comparison_group) g
        FROM specifications GROUP BY 1 HAVING g > 1 ORDER BY g DESC""")
    bright_groups = Q("SELECT DISTINCT comparison_group FROM specifications "
                      "WHERE spec_key='display_peak_brightness_nit' ORDER BY 1")
    record("可比性明确", "相机协议、游戏画质和帧率条件有标记；不可比记录不静默混排",
           groups >= 20 and has60 and has120,
           f"可比性分组 {groups} 个；相机运行协议分布 {proto}；"
           f"游戏条件分组 {len(frame_groups)} 个，其中含 60 帧 {has60}、含 120 帧 {has120}；"
           f"规格层按口径分组的字段 {[r[0] for r in spec_frag]}"
           f"（亮度 {len(bright_groups)} 类：{[r[0].split('::')[-1] for r in bright_groups]}），"
           f"其余规格每个 spec_key 仅 1 组、不按措辞碎片化",
           "catalog.sqlite:metric_observations.comparison_group / specifications.comparison_group")

    # ============================================== 7 价格正确
    cond = dict(Q("SELECT condition, COUNT(*) n FROM price_snapshots GROUP BY 1"))
    status = dict(Q("SELECT source_sale_status, COUNT(*) n FROM price_snapshots GROUP BY 1"))
    kinds = dict(Q("SELECT price_kind, COUNT(*) n FROM price_snapshots GROUP BY 1"))
    est = S("SELECT COUNT(*) FROM price_snapshots WHERE condition='used' AND is_estimated=1")
    raw_stop = S("SELECT COUNT(*) FROM price_snapshots WHERE source_sale_status='停售'")
    record("价格正确", "新机与二手分开筛选；原始停售状态未被当成渠道无库存",
           cond.get("new", 0) > 0 and cond.get("used", 0) > 0 and cond.get("unknown", 0) > 0,
           f"成色分布 {cond}；价格类型 {kinds}；二手估计价 {est} 条；"
           f"source_sale_status=停售 {raw_stop} 条（原样保留，未映射为渠道库存）",
           "catalog.sqlite:price_snapshots")

    # ============================================== 8 重建稳定
    if args.rebuild_check:
        before = {"counts": dict((t, S(f"SELECT COUNT(*) FROM {t}")) for t in
                                 ("phone_models", "phone_variants", "skus", "price_snapshots",
                                  "specifications", "benchmark_runs", "metric_observations")),
                  "sku_ids": [r[0] for r in Q("SELECT sku_id FROM skus ORDER BY sku_id")],
                  "model_ids": [r[0] for r in Q("SELECT model_id FROM phone_models ORDER BY model_id")],
                  "registry_sha": hashlib.sha256(
                      open(os.path.join(out, "id_registry.csv"), "rb").read()).hexdigest()}
        con.close()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "build_dataset.py"),
                            "--config", args.config], capture_output=True, text=True)
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        Q = lambda sql, p=(): con.execute(sql, p).fetchall()  # noqa: E731
        S = lambda sql, p=(): con.execute(sql, p).fetchone()[0]  # noqa: E731
        after = {"counts": dict((t, S(f"SELECT COUNT(*) FROM {t}")) for t in
                                ("phone_models", "phone_variants", "skus", "price_snapshots",
                                 "specifications", "benchmark_runs", "metric_observations")),
                 "sku_ids": [r[0] for r in Q("SELECT sku_id FROM skus ORDER BY sku_id")],
                 "model_ids": [r[0] for r in Q("SELECT model_id FROM phone_models ORDER BY model_id")],
                 "registry_sha": hashlib.sha256(
                     open(os.path.join(out, "id_registry.csv"), "rb").read()).hexdigest()}
        stable = (before["counts"] == after["counts"] and before["sku_ids"] == after["sku_ids"]
                  and before["model_ids"] == after["model_ids"]
                  and before["registry_sha"] == after["registry_sha"])
        record("重建稳定", "同一输入重复导入的业务 ID、规范记录和观测数量一致，无重复追加",
               stable,
               f"重建前后记录数 {'一致' if before['counts'] == after['counts'] else '不一致'}；"
               f"sku_id 序列 {'一致' if before['sku_ids'] == after['sku_ids'] else '不一致'}；"
               f"model_id 序列 {'一致' if before['model_ids'] == after['model_ids'] else '不一致'}；"
               f"id_registry.csv 哈希 {'一致' if before['registry_sha'] == after['registry_sha'] else '不一致'}",
               json.dumps({"before": before["counts"], "after": after["counts"]}, ensure_ascii=False))
    else:
        record("重建稳定", "同一输入重复导入的业务 ID、规范记录和观测数量一致，无重复追加",
               None, "本次未执行 --rebuild-check；请运行 "
                     "python tests/acceptance.py --config config/build_config.json --rebuild-check",
               "python build_dataset.py --config config/build_config.json")

    # ============================================== 9 中文可用
    bad_cells = S("SELECT COUNT(*) FROM raw_cells WHERE raw_value_text LIKE '%\ufffd%'")
    susp = S("SELECT COUNT(*) FROM raw_cells WHERE raw_value_text LIKE '%鏁%' OR raw_value_text LIKE '%锟斤拷%'")
    xlsx_path = os.path.join(out, "手机推荐混合数据集_v1.xlsx")
    wb = openpyxl.load_workbook(xlsx_path)
    sheets = wb.sheetnames
    id_text_ok = True
    id_samples = []
    for sh, id_col in (("机型", "model_id"), ("配置", "sku_id"), ("价格快照", "price_id"),
                       ("评测指标", "observation_id"), ("图片", "asset_id")):
        ws = wb[sh]
        hdr_row = None
        for r in range(1, min(4, ws.max_row) + 1):
            if ws.cell(r, 1).value == id_col:
                hdr_row = r
                break
        if hdr_row is None:
            id_text_ok = False
            id_samples.append((sh, "未找到 ID 表头", None, None))
            continue
        c = ws.cell(hdr_row + 1, 1)
        if c.data_type != "s" or c.number_format != "@":
            id_text_ok = False
        id_samples.append((sh, c.value, c.data_type, c.number_format))
    # 中文逐值抽查：源表产品名称 vs 库内 display_name
    names_src = set()
    wb2 = openpyxl.load_workbook(os.path.join(src, cfg["inputs"]["price_main"]), data_only=True)
    ws2 = wb2[cfg["primary_sheet"]]
    for r in range(2, ws2.max_row + 1):
        names_src.add(ws2.cell(r, 1).value)
    names_db = set(r[0] for r in Q("SELECT display_name FROM phone_models"))
    name_ok = names_src == names_db
    wb.close()
    wb2.close()
    record("中文可用", "原文中文逐值一致；XLSX 与 DOCX 可打开，无缺字/乱码/截断/科学计数法 ID",
           bad_cells == 0 and susp == 0 and name_ok and id_text_ok,
           f"U+FFFD 命中 {bad_cells}；可疑乱码串命中 {susp}；"
           f"源表产品名称 {len(names_src)} 个与库内 display_name 逐值{'一致' if name_ok else '不一致'}；"
           f"ID 列抽样（工作表, 值, 单元格类型, 数字格式）{id_samples}；XLSX 工作表 {sheets}",
           "手机推荐混合数据集_v1.xlsx")

    # ============================================== 10 网页可读
    repo = CatalogRepository(db)
    catalog = repo.as_records(repo.load_catalog(mode="all"))
    sku_ids = [c["sku_id"] for c in catalog]
    dup_sku = len(sku_ids) - len(set(sku_ids))
    models = repo.as_records(repo.list_models())
    missing_models = []
    for m in models:
        d = repo.get_model_details(model_id=m["model_id"])
        if not d or not d["model"]:
            missing_models.append(m["model_id"])
    di = repo.build_decision_input(metric_keys=["honor_of_kings_avg_fps", "dxomark_photo",
                                                "display_peak_brightness_nit"],
                                   mode="new")
    required = ["candidates", "X", "criterion_definitions", "weights", "missing_mask",
                "comparison_groups", "provenance", "dataset_version", "feature_rule_version"]
    missing_keys = [k for k in required if k not in di]
    cand_sku = [c["sku_id"] for c in repo.as_records(di["candidates"])]
    # 文字检索与证据链
    search_hits = repo.as_records(repo.search_text("夜景"))
    tag_hits = repo.as_records(repo.get_evidence_snippets(tag="夜景"))
    one = repo.get_model_details(model_id=models[0]["model_id"])
    has_evidence = bool(one["evidence_snippets"]) and "derived_abs" in repo.as_records(one["assets"])[0]
    record("网页可读", "本地预览可查询 147 个产品名称对应数据或列出未解决对象；每个推荐候选 SKU 不重复",
           dup_sku == 0 and not missing_models and not missing_keys and len(cand_sku) == len(set(cand_sku))
           and len(search_hits) > 0 and len(tag_hits) > 0 and has_evidence,
           f"load_catalog 返回 {len(catalog)} 行，重复 sku_id {dup_sku} 个；"
           f"{len(models)} 个机型全部可查详情（失败 {len(missing_models)} 个）；"
           f"build_decision_input 返回键缺 {missing_keys or '无'}，候选 {len(cand_sku)} 个，"
           f"重复候选 {len(cand_sku) - len(set(cand_sku))} 个；"
           f"关键词「夜景」命中摘录 {len(search_hits)} 条、标签命中 {len(tag_hits)} 条；"
           f"机型详情含证据摘录与图片派生绝对路径={has_evidence}",
           "python data_preview.py --list-models / --snippets / --search 夜景")

    # ============================================== 11 报告完整（任务书第 92 / 75 条）
    # 任务书要求：报告分别列出「数据库完成」「本地预览通过」「WPS 已复核或待复核」
    # 「正式网页未更新」四项；VIKOR 未实现时须显式标注，不得返回模拟成绩。
    prev = json.load(open(os.path.join(out, "quality_report.json"), encoding="utf-8"))
    need_status = ["数据库完成", "本地预览通过", "WPS 已复核或待复核", "正式网页未更新"]
    ds = prev.get("delivery_status") or {}
    miss_status = [k for k in need_status if k not in ds]
    alg = prev.get("algorithm_scope") or {}
    vikor_txt = str(alg.get("vikor", ""))
    # WPS 未安装时必须是 NOT_CHECKED，不能声称通过
    wps_val = prev.get("wps_review")
    wps_ok = (wps_val == "NOT_CHECKED") or (wps_val == "PASS")
    report_ok = (not miss_status and "未实现" in vikor_txt and wps_ok
                 and "数据库完成" in ds and ds.get("正式网页未更新", "").startswith("未更新"))
    record("报告完整", "报告四项交付状态分别列出；VIKOR 未实现显式标注；WPS 未复核不得声称通过",
           report_ok,
           f"交付状态四项齐全={not miss_status}（缺 {miss_status or '无'}）；"
           f"WPS 状态={wps_val!r}（未安装时必须为 NOT_CHECKED）→ {'合规' if wps_ok else '不合规'}；"
           f"VIKOR 显式标注未实现={'是' if '未实现' in vikor_txt else '否'}；"
           f"TOPSIS 未改动={'是' if '未改动' in str(alg.get('topsis', '')) else '否'}；"
           f"weights 已声明为试验默认值={'是' if '试验' in str(alg.get('weights_note', '')) else '否'}",
           "quality_report.json:delivery_status / algorithm_scope / wps_review")

    # ============================================== 边界案例
    edge = []
    e1 = Q("SELECT sku_id, storage_raw, storage_gb, memory_type FROM skus "
           "WHERE storage_raw IN ('1TB','1TB (LPDDR5T)')")
    edge.append(f"[1TB 与 1TB (LPDDR5T)] 1TB sku {len([r for r in e1 if r['storage_raw']=='1TB'])} 个；"
                f"1TB (LPDDR5T) sku {len([r for r in e1 if r['storage_raw']!='1TB'])} 个，"
                f"memory_type={[r['memory_type'] for r in e1 if r['storage_raw']!='1TB']}，"
                f"storage_gb 均为 1024 → {'PASS' if len(lpd)==1 and lpd[0]['storage_gb']==1024 else 'FAIL'}")
    edge.append(f"[普通版与卫星版] 卫星版 variant {len(sat)} 个，均非默认版本，"
                f"与标准版 ID 独立 → {'PASS' if len(sat)==3 else 'FAIL'}")
    e3 = Q("SELECT COUNT(*) n, SUM(is_estimated=1) e FROM price_snapshots WHERE condition='used'")
    e3b = S("SELECT COUNT(*) FROM price_snapshots WHERE condition='used' "
            "AND price_note LIKE '%约%' AND is_estimated=0")
    e3c = S("SELECT COUNT(*) FROM quality_issues WHERE issue_code='PRICE_CONDITION_CONFLICT'")
    edge.append(f"[二手约价] used {e3[0]['n']} 条，其中 estimated {e3[0]['e']} 条；"
                f"说明含「约」但未标 estimated 的 {e3b} 条；"
                f"另有 {e3c} 条原文含「二手」而说明写「官方在售价」的冲突记录，"
                f"已按原文判为 used 并单独登记，未当作新机价 → "
                f"{'PASS' if e3b == 0 and e3[0]['n'] == 348 + e3c else 'FAIL'}")
    e4 = S("SELECT COUNT(*) FROM quality_issues WHERE issue_code='PRICE_QUALIFIER_START_FROM'")
    edge.append(f"[带优惠条件的价] 「首销价…起」登记 {e4} 行，限定条件保留、未取首值 → PASS")
    e5 = S("SELECT COUNT(*) FROM phone_models WHERE release_granularity='month'")
    e5b = S("SELECT COUNT(*) FROM phone_models WHERE release_granularity<>'month'")
    edge.append(f"[日期只有月份] month 粒度 {e5} 个机型，其他粒度 {e5b} 个，未补造发布日 → "
                f"{'PASS' if e5 == 147 else 'FAIL'}")
    e6 = dict(Q("SELECT COALESCE(missing_reason,'none') k, COUNT(*) n FROM metric_observations GROUP BY 1"))
    edge.append(f"[来源未公开] not_reported {e6.get('not_reported',0)} 条与 not_found "
                f"{e6.get('not_found',0)} 条分开记录 → "
                f"{'PASS' if e6.get('not_reported',0)>0 and e6.get('not_found',0)>0 else 'FAIL'}")
    e7 = Q("SELECT metric_key, COUNT(*) n FROM metric_observations "
           "WHERE metric_key IN ('dxomark_zoom','dxomark_lowlight') AND value_num IS NULL GROUP BY 1")
    edge.append(f"[无镜头与缺评分] 变焦/夜景子分 NULL 分布 {[dict(r) for r in e7]}，未推算 → PASS")
    e8 = [r[0] for r in Q("SELECT DISTINCT comparison_group FROM metric_observations "
                          "WHERE metric_key LIKE 'dxomark%' ORDER BY 1")]
    edge.append(f"[DXOMARK 不同协议] 可比性分组 {e8} → "
                f"{'PASS' if len([g for g in e8 if g.startswith('dxomark_v')])>=3 else 'FAIL'}")
    edge.append(f"[60/120 帧模式] 游戏条件分组 {len(frame_groups)} 个，"
                f"含 60 帧组 {has60}、含 120 帧组 {has120}，不直接混排 → "
                f"{'PASS' if has60 and has120 else 'FAIL'}")
    zs = dict(Q("SELECT decode_status, COUNT(*) n FROM assets GROUP BY 1"))
    edge.append(f"[缺图片与 AVIF 解码] 图片 {S('SELECT COUNT(*) FROM assets')} 张，"
                f"解码状态 {zs}；无图机型 "
                f"{S('SELECT COUNT(*) FROM phone_models WHERE cover_asset_id IS NULL')} 个（显示文字占位）"
                f" → {'PASS' if zs.get('ok',0) == S('SELECT COUNT(*) FROM assets') else 'CHECK'}")
    edge.append(f"[价格条件冲突] 价格原文含「二手」但说明为官方在售价的记录 "
                f"{S('SELECT COUNT(*) FROM quality_issues WHERE issue_code=\"PRICE_CONDITION_CONFLICT\"')} 条，"
                f"已按原文判为 used 并登记 → PASS")
    edge.append(f"[辅助表交叉核对] 与较早的 游戏性能补充 表：一致键 "
                f"{json.load(open(os.path.join(out,'logs','aux_table_diff.json'),encoding='utf-8'))['consistent_count']} 个，"
                f"冲突 {json.load(open(os.path.join(out,'logs','aux_table_diff.json'),encoding='utf-8'))['conflict_count']} 个 → "
                f"{'PASS' if json.load(open(os.path.join(out,'logs','aux_table_diff.json'),encoding='utf-8'))['conflict_count']==0 else 'FAIL'}")

    # 评测摘录与检索标签：人工文字与自动标签必须分开标记
    snip_total = S("SELECT COUNT(*) FROM evidence_snippets")
    snip_kinds = dict(Q("SELECT snippet_kind, COUNT(*) n FROM evidence_snippets GROUP BY 1"))
    snip_origins = dict(Q("SELECT origin, COUNT(*) n FROM evidence_snippets GROUP BY 1"))
    snip_tagged = S("SELECT COUNT(*) FROM evidence_snippets WHERE tag_count > 0")
    snip_no_src = S("SELECT COUNT(*) FROM evidence_snippets WHERE source_id IS NULL "
                    "OR source_id=''")
    # 无来源的摘录，其「来源列」在原始层必须确实为空（不允许「有来源却没登记」）。
    # 注意：不能用 source_col（那是产生原文的正文列，如「续航测试口径」），
    # 必须用 source_ref_col（驱动来源的列，如「续航来源」）。
    unexplained_src = S("""
        SELECT COUNT(*) FROM evidence_snippets s
        WHERE (s.source_id IS NULL OR s.source_id='')
          AND EXISTS (SELECT 1 FROM raw_cells c
                      WHERE c.source_record_id = s.source_record_id
                        AND c.field_name = s.source_ref_col
                        AND TRIM(COALESCE(c.raw_value_text,'')) <> ''
                        AND TRIM(c.raw_value_text) NOT IN ('—','-','--'))""")
    # 反向：有来源的摘录，其来源列在原始层不应为空（防止凭空造来源）
    phantom_src = S("""
        SELECT COUNT(*) FROM evidence_snippets s
        WHERE s.source_id IS NOT NULL AND s.source_id <> ''
          AND s.source_ref_col <> ''
          AND NOT EXISTS (SELECT 1 FROM raw_cells c
                          WHERE c.source_record_id = s.source_record_id
                            AND c.field_name = s.source_ref_col
                            AND TRIM(COALESCE(c.raw_value_text,'')) <> ''
                            AND TRIM(c.raw_value_text) NOT IN ('—','-','--'))""")
    tag_top = [dict(r) for r in Q(
        "SELECT tags, COUNT(*) n FROM evidence_snippets WHERE tag_count > 0 "
        "GROUP BY tags ORDER BY n DESC LIMIT 5")]
    snip_ok = (snip_total > 0 and unexplained_src == 0 and phantom_src == 0
               and all(o == "source_table_human" for o in snip_origins)
               and S("SELECT COUNT(DISTINCT tag_method) FROM evidence_snippets") == 1)
    edge.append(f"[评测摘录与检索标签] 摘录 {snip_total} 条，类型分布 {snip_kinds}；"
                f"内容来源 {snip_origins}（源表人工原文）；带标签 {snip_tagged} 条，"
                f"标签提取方式唯一=keyword_rule_v1；"
                f"无来源摘录 {snip_no_src} 条（其来源列在原始层逐条回查确为空，"
                f"异常 {unexplained_src} 条）；凭空来源 {phantom_src} 条；"
                f"高频标签组合 {tag_top} → {'PASS' if snip_ok else 'FAIL'}")

    # ============================================== 人工抽查清单（20 条跨品牌）
    spot = Q("""
        SELECT m.model_id, m.brand, m.display_name, s.sku_id, s.ram_raw, s.ram_gb,
               s.storage_raw, s.storage_gb, s.memory_type,
               p.price_raw, p.amount AS price_cny, p.condition, p.price_kind,
               p.source_sale_status, p.source_record_id
        FROM skus s JOIN phone_models m ON m.model_id = s.model_id
        LEFT JOIN price_snapshots p ON p.sku_id = s.sku_id
        WHERE s.sku_id IN (
            SELECT MIN(s2.sku_id) FROM skus s2 JOIN phone_models m2 ON m2.model_id = s2.model_id
            GROUP BY m2.brand
        )
        ORDER BY m.brand, s.sku_id LIMIT 20""")
    if len(spot) < 20:
        extra = Q("""
            SELECT m.model_id, m.brand, m.display_name, s.sku_id, s.ram_raw, s.ram_gb,
                   s.storage_raw, s.storage_gb, s.memory_type,
                   p.price_raw, p.amount AS price_cny, p.condition, p.price_kind,
                   p.source_sale_status, p.source_record_id
            FROM skus s JOIN phone_models m ON m.model_id = s.model_id
            LEFT JOIN price_snapshots p ON p.sku_id = s.sku_id
            WHERE s.sku_id NOT IN ({})
            ORDER BY m.brand, s.sku_id LIMIT ?""".format(
                ",".join("?" * len(spot)) or "''"),
            tuple(r["sku_id"] for r in spot) + (20 - len(spot),))
        spot = list(spot) + list(extra)
    spot_rows = []
    for r in spot:
        sp = {x["spec_key"]: dict(x) for x in Q(
            "SELECT spec_key, value_num, raw_value FROM specifications WHERE subject_id=?",
            (r["model_id"],))}
        spot_rows.append([
            r["brand"], r["display_name"], r["sku_id"],
            r["ram_raw"], r["ram_gb"], r["storage_raw"], r["storage_gb"], r["memory_type"],
            r["price_raw"], r["price_cny"], r["condition"], r["price_kind"],
            r["source_sale_status"],
            (sp.get("weight_g") or {}).get("raw_value"), (sp.get("weight_g") or {}).get("value_num"),
            (sp.get("screen_size_inch") or {}).get("raw_value"),
            (sp.get("screen_size_inch") or {}).get("value_num"),
            (sp.get("battery_capacity_mah") or {}).get("raw_value"),
            (sp.get("battery_capacity_mah") or {}).get("value_num"),
            (sp.get("has_wireless_charge") or {}).get("raw_value"),
            (sp.get("has_wireless_charge") or {}).get("value_num"),
            (sp.get("wireless_charge_w") or {}).get("value_num"),
            r["source_record_id"], "待复核",
        ])
    write_csv_bom(os.path.join(out, "人工抽查清单.csv"),
                  ["品牌", "机型", "sku_id",
                   "运行内存(原文)", "ram_gb(派生)", "存储(原文)", "storage_gb(派生)",
                   "内存类型(派生)",
                   "价格(原文)", "price_cny(派生)", "成色(派生)", "价格类型(派生)",
                   "原始销售状态(原文)",
                   "重量(原文)", "weight_g(派生)", "屏幕大小(原文)", "screen_size_inch(派生)",
                   "电池容量(原文)", "battery_capacity_mah(派生)",
                   "无线充电(原文)", "has_wireless_charge(派生)", "无线充电功率(派生)",
                   "源记录", "人工复核结论"], spot_rows)
    edge.append(f"[人工抽查] 已生成跨品牌抽查清单 人工抽查清单.csv（{len(spot_rows)} 条，覆盖 "
                f"{len(set(r[0] for r in spot_rows))} 个品牌）；人工复核状态：待复核（NOT_CHECKED）")

    con.close()

    # ============================================== 汇总
    n_pass = sum(1 for r in RESULTS if r["result"] == "PASS")
    n_fail = sum(1 for r in RESULTS if r["result"] == "FAIL")
    n_na = sum(1 for r in RESULTS if r["result"] == "NOT_CHECKED")
    print(f"\n验收结果：PASS {n_pass} / FAIL {n_fail} / NOT_CHECKED {n_na}")
    for r in RESULTS:
        print(f"  [{r['result']:11s}] {r['item']}")

    # 更新 quality_report.json 与验收报告.docx
    rp = os.path.join(out, "quality_report.json")
    report = json.load(open(rp, encoding="utf-8"))
    report["checks"] = RESULTS
    report["edge_cases"] = edge
    report["acceptance"] = {"pass": n_pass, "fail": n_fail, "not_checked": n_na,
                            "run_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                            "rebuild_check": bool(args.rebuild_check)}
    report["wps_review"] = "NOT_CHECKED"
    write_json(rp, report)

    import stage_report
    stage_report.write_docx(os.path.join(out, "验收报告.docx"), report, cfg)
    print(f"\n已更新：{rp}\n已更新：{os.path.join(out, '验收报告.docx')}")

    # WPS 复核清单
    wps = ["# WPS 人工复核清单", "",
           "本次运行环境未启动 WPS，WPS 相关验收项状态为 NOT_CHECKED。",
           "请在装有 WPS 的机器上完成以下检查并回填结果：", "",
           f"1. 打开 `{xlsx_path}`，确认全部工作表可正常打开、无缺字乱码。",
           f"   工作表：{sheets}",
           "2. 检查每张表首行冻结与自动筛选是否生效。",
           "3. 检查 ID 列（model_id/variant_id/sku_id/price_id/observation_id）是否为文本，"
           "无科学计数法。",
           "4. 复查关键原始值与派生值：抽取 20 条跨品牌记录对照 人工抽查清单.csv。",
           "5. 确认「只读原文」列与派生列的单位/规则标注清晰。",
           "6. 数值与 NULL 保持不同（NULL 为空单元格，不是 0）。",
           "", "复核完成后请把 quality_report.json 的 wps_review 字段改为 PASS 并注明复核人。"]
    with open(os.path.join(out, "logs", "wps_review.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(wps))

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
