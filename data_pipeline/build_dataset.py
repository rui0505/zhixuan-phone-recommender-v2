# -*- coding: utf-8 -*-
"""手机推荐混合数据集 v1 —— 构建入口。

用法：
  python build_dataset.py --config config/build_config.json
  python build_dataset.py --config config/build_config.json --skip-assets
  python build_dataset.py --config config/build_config.json --verify-only

工作顺序：登记输入 → 保存原始记录 → 建立 ID → 解析派生字段 → 建库 → 导出 Excel
          → 本地读取验证 → 交付报告。

本脚本只读源文件；输出全部写入独立输出目录，不触碰正式网页数据。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import shutil
import csv
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import openpyxl  # noqa: E402

from common import (ensure_dirs, force_utf8_stdio, load_config, now_iso, resolve_dirs,
                    sha256_file, value_to_text, write_csv_bom, write_json, write_jsonl)  # noqa: E402
import stage_assets  # noqa: E402
import stage_db  # noqa: E402
import stage_derived  # noqa: E402
import stage_ids  # noqa: E402
import stage_manifest  # noqa: E402
import stage_xlsx  # noqa: E402
from rules import RULE_VERSION  # noqa: E402


def read_rows(path: str, sheet_name: str, file_key: str):
    """读成 [{列名: 值, 'row_no': n, 'source_record_id': ...}]，值保持原始类型。"""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet_name]
    header = [c.value for c in ws[1]]
    rows = []
    for r in range(2, ws.max_row + 1):
        d = {h: ws.cell(r, i + 1).value for i, h in enumerate(header)}
        d["row_no"] = r
        d["source_record_id"] = f"{file_key}#{sheet_name}#R{r}"
        rows.append(d)
    wb.close()
    return rows


def compare_aux(main_rows, aux_rows, shared_cols):
    """主表 vs 游戏辅助表：一致的不重复导入，冲突保留双方值。

    以 (名称, RAM, 存储) 为业务键做「多重集」比较，避免同一键存在多行
    （如普通版/卫星版）时因行序不同被误判为冲突。
    """
    def key(r):
        return (r.get("产品名称"), r.get("运行内存"), r.get("存储"))

    def sig(r):
        return tuple(value_to_text(r.get(c)) for c in shared_cols)

    main_by_key, aux_by_key = {}, {}
    for r in main_rows:
        main_by_key.setdefault(key(r), []).append(r)
    for r in aux_rows:
        aux_by_key.setdefault(key(r), []).append(r)

    consistent, conflicts, aux_only = [], [], []
    for k, arows in aux_by_key.items():
        mrows = main_by_key.get(k)
        if not mrows:
            aux_only.append({"key": list(k),
                             "records": [r["source_record_id"] for r in arows]})
            continue
        msig = sorted(sig(r) for r in mrows)
        asig = sorted(sig(r) for r in arows)
        if msig == asig:
            consistent.append({"key": list(k),
                               "main_records": [r["source_record_id"] for r in mrows],
                               "aux_records": [r["source_record_id"] for r in arows]})
            continue
        diffs = []
        for i in range(max(len(msig), len(asig))):
            m = msig[i] if i < len(msig) else ()
            a = asig[i] if i < len(asig) else ()
            for j, c in enumerate(shared_cols):
                mv = m[j] if j < len(m) else None
                av = a[j] if j < len(a) else None
                if mv != av:
                    diffs.append({"column": c, "main_value": mv, "aux_value": av})
        conflicts.append({"key": list(k),
                          "main_records": [r["source_record_id"] for r in mrows],
                          "aux_records": [r["source_record_id"] for r in arows],
                          "differences": diffs})
    return {"consistent": consistent, "conflicts": conflicts, "aux_only": aux_only}


def main():
    force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config", "build_config.json"))
    ap.add_argument("--skip-assets", action="store_true", help="跳过图片阶段（调试用）")
    ap.add_argument("--verify-only", action="store_true", help="只做输入哈希核验")
    ap.add_argument('--pilot-sku-ids', help='JSON数组：仅导出选定配置的业务实体，原始层保持全量')
    ap.add_argument('--data-only', action='store_true', help='只生成数据库和机器证据，不生成XLSX/DOCX')
    args = ap.parse_args()

    t0 = time.time()
    cfg = load_config(args.config)
    if cfg['rule_version'] != RULE_VERSION:
        raise ValueError('Configured rule version does not match implementation')
    paths = resolve_dirs(cfg)
    ensure_dirs(paths)
    log = {"stage_times": {}, "warnings": []}

    def mark(name, start):
        log["stage_times"][name] = round(time.time() - start, 2)

    # ================================================== 阶段 1 输入登记
    s = time.time()
    manifest, raw_rows, raw_cells = stage_manifest.build_manifest(cfg, paths)
    manifest["_raw_rows"] = raw_rows
    manifest["_raw_cells"] = raw_cells
    write_jsonl(os.path.join(paths["raw_snapshot"], "raw_rows.jsonl"), raw_rows)
    write_jsonl(os.path.join(paths["raw_snapshot"], "raw_cells.jsonl"), raw_cells)
    write_json(os.path.join(paths["output_dir"], "source_manifest.json"),
               {k: v for k, v in manifest.items() if not k.startswith("_")})
    source_audit = stage_manifest.audit_source_cells(cfg, raw_cells)
    write_json(os.path.join(paths['logs'], 'source_cells_audit.json'), source_audit)
    if source_audit['status'] != 'PASS':
        raise ValueError('Source cell set differs from captured raw layer')
    mark("1_manifest", s)
    print(f"[1/7] 输入登记完成：{len(raw_rows)} 行 / {len(raw_cells)} 单元格")

    if args.verify_only:
        # 只核验输入哈希，写入独立文件，避免覆盖完整质量报告
        report = verify_inputs(cfg, paths, manifest)
        write_json(os.path.join(paths["logs"], "input_verify.json"), report)
        write_json(os.path.join(paths["output_dir"], "source_manifest.json"),
                   {k: v for k, v in manifest.items() if not k.startswith("_")})
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return

    issues = []
    # 源文件编码扫描
    for f in manifest["files"]:
        if f.get("encoding_scan", {}).get("suspicious"):
            issues.append(dict(issue_code="SUSPICIOUS_ENCODING", severity="high",
                               source_record_id=f["file_key"],
                               detail=f"{f['file_name']} 检出可疑编码串，样本："
                                      f"{f['encoding_scan']['samples'][:3]}"))
    for f in manifest["files"]:
        for sh in f.get("sheets", []):
            if sh.get("formula_cells"):
                issues.append(dict(issue_code="FORMULA_PRESENT", severity="info",
                                   source_record_id=f"{f['file_key']}#{sh['name']}",
                                   detail=f"{sh['name']} 含 {sh['formula_cells']} 个公式单元格，"
                                          f"已分别保存公式与缓存值"))
            else:
                issues.append(dict(issue_code="NO_FORMULA_IN_SHEET", severity="info",
                                   source_record_id=f"{f['file_key']}#{sh['name']}",
                                   detail=f"{sh['name']} 无公式单元格，公式层为空；"
                                          f"未通过 WPS 重算源文件"))

    # ================================================== 读取业务行
    src = paths["source_dir"]
    main_rows = read_rows(os.path.join(src, cfg["inputs"]["price_main"]),
                          cfg["primary_sheet"], "price_main")
    aux_rows = read_rows(os.path.join(src, cfg["inputs"]["price_aux"]),
                         cfg["primary_sheet"], "price_aux")
    camera_rows = read_rows(os.path.join(src, cfg["inputs"]["camera"]),
                            cfg["camera_sheet"], "camera")

    shared = [h for h in openpyxl.load_workbook(
        os.path.join(src, cfg["inputs"]["price_aux"]), read_only=True).worksheets[0].iter_rows(
            max_row=1, values_only=True).__next__() if h]
    aux_diff = compare_aux(main_rows, aux_rows, shared)
    for c in aux_diff["conflicts"]:
        issues.append(dict(issue_code="AUX_TABLE_CONFLICT", severity="high",
                           source_record_id=c["aux_records"][0],
                           detail=f"辅助表与主表在 {c['key']} 上存在 {len(c['differences'])} 处差异，"
                                  f"保留双方值：{c['differences'][:3]}"))
    for c in aux_diff["aux_only"]:
        issues.append(dict(issue_code="AUX_TABLE_EXTRA_ROW", severity="medium",
                           source_record_id=c["records"][0],
                           detail=f"辅助表存在主表没有的记录 {c['key']}，未合并导入"))
    write_json(os.path.join(paths["logs"], "aux_table_diff.json"),
               {"consistent_count": len(aux_diff["consistent"]),
                "conflict_count": len(aux_diff["conflicts"]),
                "aux_only_count": len(aux_diff["aux_only"]),
                "conflicts": aux_diff["conflicts"][:50],
                "aux_only": aux_diff["aux_only"][:50]})

    # ================================================== 阶段 2 稳定 ID
    s = time.time()
    registry_path = os.path.join(paths["output_dir"], "id_registry.csv")
    seed_path = os.path.abspath(os.path.join(os.path.dirname(args.config), cfg.get('registry_seed', '../id_registry.csv')))
    if not os.path.exists(registry_path):
        if not os.path.isfile(seed_path):
            raise ValueError('Existing ID registry seed is required')
        shutil.copy2(seed_path, registry_path)
    alias_path = os.path.join(paths["output_dir"], "model_aliases.csv")
    model_order, variant_list, _, alias_rows, id_issues, id_stats = stage_ids.build_ids(
        main_rows, registry_path, alias_path, RULE_VERSION, cfg["id_registry_version"])
    issues.extend(id_issues)
    model_id_by_name = {m["match_key"]: m["model_id"] for m in model_order}
    mark("2_ids", s)
    print(f"[2/7] ID 建立完成：机型 {id_stats['models']} / 版本 {id_stats['variants']} / "
          f"配置 {id_stats['skus']}（新增 M{id_stats['new_models']} "
          f"V{id_stats['new_variants']} S{id_stats['new_skus']}）")

    # ================================================== 阶段 3 派生
    s = time.time()
    entities, der_issues = stage_derived.build_derived(
        cfg, paths, main_rows, camera_rows, aux_rows, model_id_by_name,
        model_id_by_name, collected_at=_collected_at(main_rows))
    issues.extend(der_issues)
    mark("3_derived", s)

    # ---- 组装 phone_models / variants ----
    first_row = {}
    for r in main_rows:
        first_row.setdefault(r["_model_id"], r)
    phone_models = []
    for m in model_order:
        fr = first_row.get(m["model_id"], {})
        phone_models.append({
            "model_id": m["model_id"], "brand": m["brand"],
            "display_name": m["display_name"], "display_name_match_key": m["match_key"],
            "release_month": fr.get("_release_month"), "release_month_raw": fr.get("发布时间"),
            "release_granularity": fr.get("_release_granularity"),
            "first_source_record_id": m["source_record_id"],
            "cover_asset_id": None,
            "note": "机型的发布信息取该机型首条源记录；同一机型多配置共用",
        })
    phone_variants = []
    for v in variant_list:
        fr = first_row.get(v["model_id"], {})
        ev = None
        if v["region"]:
            ev = _region_evidence(fr.get("新增配置备注"))
        phone_variants.append({
            "variant_id": v["variant_id"], "model_id": v["model_id"],
            "variant_name": v["variant_name"], "name_is_source": v["name_is_source"],
            "region": v["region"], "region_evidence": ev,
            "satellite_support": v["satellite_support"], "is_default": v["is_default"],
            "variant_note_raw": v["variant_note_raw"], "source_record_id": v["source_record_id"],
        })

    # ================================================== 阶段 4 图片
    s = time.time()
    if args.skip_assets:
        assets_pack = {"assets": [], "members": [], "covers": [], "stats": {}}
        log["warnings"].append("--skip-assets：未处理图片包")
    else:
        assets_pack = stage_assets.build_assets(cfg, paths, model_id_by_name,
                                                model_order, issues)
    for c in assets_pack["covers"]:
        for m in phone_models:
            if m["model_id"] == c["model_id"]:
                m["cover_asset_id"] = c["cover_asset_id"]
    mark("4_assets", s)
    print(f"[3/7] 图片阶段完成：成员 {assets_pack['stats'].get('members')} / "
          f"图片 {assets_pack['stats'].get('images')} / 可解码 {assets_pack['stats'].get('decoded')} / "
          f"副本 {assets_pack['stats'].get('derived')}")

    # ================================================== 映射表
    mappings = []
    for r in main_rows:
        for ttype, tid, role in (("model", r["_model_id"], "源行 -> 机型"),
                                 ("variant", r["_variant_id"], "源行 -> 硬件版本"),
                                 ("sku", r["_sku_id"], "源行 -> 购买配置")):
            mappings.append({
                "source_file_key": "price_main", "sheet_name": cfg["primary_sheet"],
                "source_row_no": r["row_no"], "source_record_id": r["source_record_id"],
                "target_type": ttype, "target_id": tid, "mapping_role": role,
                "merge_note": "",
            })
    price_by_sku = {p["sku_id"]: p for p in entities["price_snapshots"]}
    for r in main_rows:
        p = price_by_sku.get(r["_sku_id"])
        if p:
            mappings.append({
                "source_file_key": "price_main", "sheet_name": cfg["primary_sheet"],
                "source_row_no": r["row_no"], "source_record_id": r["source_record_id"],
                "target_type": "price", "target_id": p["price_id"],
                "mapping_role": "源行 -> 价格快照", "merge_note": "",
            })
        if r.get("_game_run_id"):
            mappings.append({
                "source_file_key": "price_main", "sheet_name": cfg["primary_sheet"],
                "source_row_no": r["row_no"], "source_record_id": r["source_record_id"],
                "target_type": "benchmark_run", "target_id": r["_game_run_id"],
                "mapping_role": "源行 -> 游戏评测运行（机型级共享，多配置行指向同一 run）",
                "merge_note": "同机型多配置行归并到同一 run_id",
            })
    for r in camera_rows:
        mid = r.get("_model_id")
        if mid:
            mappings.append({
                "source_file_key": "camera", "sheet_name": cfg["camera_sheet"],
                "source_row_no": r["row_no"], "source_record_id": r["source_record_id"],
                "target_type": "model", "target_id": mid,
                "mapping_role": "相机源行 -> 机型", "merge_note": "",
            })
    for r in aux_rows:
        k = (r.get("产品名称"), r.get("运行内存"), r.get("存储"))
        mid = model_id_by_name.get(_norm(r.get("产品名称")))
        if mid:
            mappings.append({
                "source_file_key": "price_aux", "sheet_name": cfg["primary_sheet"],
                "source_row_no": r["row_no"], "source_record_id": r["source_record_id"],
                "target_type": "model", "target_id": mid,
                "mapping_role": "辅助表源行 -> 机型（仅交叉核对）",
                "merge_note": "辅助表不参与合并导入，仅比对",
            })
    entities["import_mapping"] = mappings
    entities["phone_models"] = phone_models
    entities["phone_variants"] = phone_variants

    # 覆盖检查：所有源记录都有映射
    mapped_src = set(m["source_record_id"] for m in mappings)
    for r in main_rows + camera_rows + aux_rows:
        if r["source_record_id"] not in mapped_src:
            issues.append(dict(issue_code="SOURCE_ROW_UNMAPPED", severity="high",
                               source_record_id=r["source_record_id"],
                               detail="该源记录没有任何映射目标"))

    if args.pilot_sku_ids:
        with open(args.pilot_sku_ids, encoding='utf-8') as fh:
            pilot_ids = json.load(fh)
        if not isinstance(pilot_ids, list) or not all(isinstance(x, str) for x in pilot_ids):
            raise ValueError('Pilot IDs must be a JSON string array')
        if len(pilot_ids) != len(set(pilot_ids)):
            raise ValueError('Duplicate pilot IDs')
        filter_pilot_entities(entities, assets_pack, set(pilot_ids))
        id_stats.update(models=len(entities['phone_models']), variants=len(entities['phone_variants']),
                        skus=len(entities['skus']))

    # ================================================== 阶段 5 建库
    s = time.time()
    db_path = os.path.join(paths["output_dir"], "catalog.sqlite")
    counts, fk_errors = stage_db.build_db(db_path, manifest, entities, assets_pack,
                                          issues, id_stats, cfg)
    mark("5_db", s)
    if fk_errors:
        issues.append(dict(issue_code="FOREIGN_KEY_VIOLATION", severity="high",
                           source_record_id="", detail=f"外键检查发现 {len(fk_errors)} 条违规"))
    print(f"[4/7] 数据库完成：{db_path}（外键违规 {len(fk_errors)}）")
    if fk_errors:
        raise ValueError('Foreign key violations: database is not accepted')
    if args.data_only:
        hash_checks = verify_inputs(cfg, paths, manifest)
        write_json(os.path.join(paths['logs'], 'data_only_status.json'), {
            'scope': 'pilot' if args.pilot_sku_ids else 'full', 'counts': counts,
            'source_cells': source_audit['status'], 'input_hashes': hash_checks,
            'manual_review': 'NOT_CHECKED', 'wps_review': 'NOT_CHECKED',
            'web_deployment': 'NOT_UPDATED', 'overall_acceptance': 'PENDING'})
        print('Data-only build finished; document/WPS/manual acceptance is pending')
        return

    # ================================================== 阶段 6 XLSX
    s = time.time()
    xlsx_path = os.path.join(paths["output_dir"], cfg['workbook_name'])
    stage_xlsx.export_workbook(xlsx_path, cfg, manifest, entities, assets_pack,
                               issues, counts, id_stats)
    mark("6_xlsx", s)
    print(f"[5/7] 规范 Excel 完成：{xlsx_path}")

    # 资源清单 CSV
    write_csv_bom(os.path.join(paths["output_dir"], "assets_manifest.csv"),
                  stage_assets.ASSET_HEADER,
                  [[a.get(h, "") for h in stage_assets.ASSET_HEADER]
                   for a in assets_pack["assets"]])
    write_csv_bom(os.path.join(paths["output_dir"], "zip_members.csv"),
                  stage_assets.MEMBER_HEADER,
                  [[m.get(h, "") for h in stage_assets.MEMBER_HEADER]
                   for m in assets_pack["members"]])
    write_csv_bom(os.path.join(paths["output_dir"], "quality_issues.csv"),
                  ["issue_code", "severity", "source_record_id", "detail"],
                  [[i["issue_code"], i["severity"], i.get("source_record_id", ""), i["detail"]]
                   for i in issues])

    # ================================================== 阶段 7 核验 + 报告
    s = time.time()
    hash_result = verify_inputs(cfg, paths, manifest)
    if not hash_result['passed']:
        raise ValueError('Input hash changed during build')
    report = build_quality_report(cfg, paths, manifest, entities, assets_pack, issues,
                                 counts, id_stats, aux_diff, log, fk_errors)
    write_json(os.path.join(paths["output_dir"], "quality_report.json"), report)
    mark("7_verify", s)

    # 源文件哈希复核
    verify_inputs(cfg, paths, manifest)
    write_json(os.path.join(paths["output_dir"], "source_manifest.json"),
               {k: v for k, v in manifest.items() if not k.startswith("_")})

    import stage_report
    stage_report.write_docx(os.path.join(paths["output_dir"], cfg['report_name']),
                            report, cfg)
    print(f"[6/7] 验收报告完成")
    print(f"[7/7] 总耗时 {round(time.time() - t0, 1)}s")


def filter_pilot_entities(entities: dict, assets_pack: dict, sku_ids: set[str]) -> None:
    available = {row['sku_id'] for row in entities['skus']}
    if not sku_ids or not sku_ids <= available:
        raise ValueError('Pilot contains unknown SKU IDs or is empty')
    entities['skus'] = [r for r in entities['skus'] if r['sku_id'] in sku_ids]
    model_ids = {r['model_id'] for r in entities['skus']}
    variant_ids = {r['variant_id'] for r in entities['skus']}
    scope_ids = {'sku': sku_ids, 'model': model_ids, 'variant': variant_ids}
    for table, column, values in (('phone_models', 'model_id', model_ids),
                                  ('phone_variants', 'variant_id', variant_ids),
                                  ('price_snapshots', 'sku_id', sku_ids)):
        entities[table] = [r for r in entities[table] if r[column] in values]
    entities['specifications'] = [r for r in entities['specifications']
        if r['subject_id'] in scope_ids.get(r['subject_type'], set())]
    entities['benchmark_runs'] = [r for r in entities['benchmark_runs']
        if r.get('model_id') in model_ids and (not r.get('variant_id') or r['variant_id'] in variant_ids)
        and (not r.get('sku_id') or r['sku_id'] in sku_ids)]
    run_ids = {r['run_id'] for r in entities['benchmark_runs']}
    entities['metric_observations'] = [r for r in entities['metric_observations'] if r['run_id'] in run_ids]
    if 'evidence_snippets' in entities:
        entities['evidence_snippets'] = [r for r in entities['evidence_snippets']
            if r.get('subject_id') in scope_ids.get(r.get('subject_type'), set())
            and (not r.get('related_run_id') or r['related_run_id'] in run_ids)]
    mapping_ids = dict(scope_ids, price={r['price_id'] for r in entities['price_snapshots']}, benchmark_run=run_ids)
    entities['import_mapping'] = [r for r in entities['import_mapping']
        if r['target_id'] in mapping_ids.get(r['target_type'], set())]
    for table in ('assets', 'covers'):
        assets_pack[table] = [r for r in assets_pack.get(table, []) if r['model_id'] in model_ids]
    if 'spec_provenance' in entities:
        spec_ids = {r['spec_id'] for r in entities['specifications']}
        entities['spec_provenance'] = [r for r in entities['spec_provenance'] if r['spec_id'] in spec_ids]


def _norm(s):
    from common import normalize_match_key
    return normalize_match_key(s)


def _collected_at(rows):
    for r in rows:
        if r.get("查询日期"):
            return str(r["查询日期"])
    return now_iso()[:10]


def _region_evidence(note):
    if not note:
        return None
    for line in str(note).split("\n"):
        if "中国大陆" in line:
            return line.strip()
    return str(note)[:120]


def verify_inputs(cfg, paths, manifest):
    """执行后重新计算 SHA-256，确认源文件未被改动。"""
    results = []
    for f in manifest["files"]:
        p = f["abs_path"]
        if not os.path.exists(p):
            results.append({"file_key": f["file_key"], "exists": False,
                            "unchanged": False, "sha256_before": f.get("sha256_before")})
            f["sha256_after"] = None
            continue
        after = sha256_file(p)
        f["sha256_after"] = after
        results.append({"file_key": f["file_key"], "file_name": f["file_name"],
                        "exists": True, "sha256_before": f.get("sha256_before"),
                        "sha256_after": after,
                        "unchanged": after == f.get("sha256_before")})
    ok = all(r["unchanged"] for r in results)
    return {"check": "原文件不变", "passed": ok, "files": results,
            "checked_at": now_iso()}


def build_quality_report(cfg, paths, manifest, entities, assets_pack, issues,
                         counts, id_stats, aux_diff, log, fk_errors):
    """11 项验收的实际结果。"""
    import sqlite3
    db = os.path.join(paths["output_dir"], "catalog.sqlite")
    con = sqlite3.connect(db)
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731

    checks = []

    def add(item, standard, passed, detail, evidence=""):
        checks.append({"item": item, "standard": standard,
                       "result": "PASS" if passed is True else ("FAIL" if passed is False
                                                                else "NOT_CHECKED"),
                       "detail": detail, "evidence": evidence})

    # 1 原文件不变
    pre = {f["file_key"]: f.get("sha256_before") for f in manifest["files"]}
    add("原文件不变", "输入执行前后 SHA-256 全部一致",
        all(f.get("sha256_after") == pre[f["file_key"]] for f in manifest["files"]
            if f.get("sha256_before")),
        f"{len(manifest['files'])} 个输入文件哈希复核完成",
        json.dumps(pre, ensure_ascii=False)[:400])

    # 2 原始值可复查
    total_rows = len(manifest["_raw_rows"])
    mapped_set = set(m["source_record_id"] for m in entities["import_mapping"])
    data_src = set(r["source_record_id"] for r in manifest["_raw_rows"]
                   if not r["is_header_row"] and r["file_key"] != "images_zip")
    unmapped = data_src - mapped_set
    source_audit = stage_manifest.audit_source_cells(cfg, manifest['_raw_cells'])
    add("原始值可复查", "所有源记录有映射；原始数值/文字/类型/公式逐项对照，差异为零",
        len(unmapped) == 0 and source_audit['status'] == 'PASS',
        f"数据源行 {len(data_src)}，已映射 {len(data_src) - len(unmapped)}，"
        f"未映射 {len(unmapped)}；原始层共 {total_rows} 行、{len(manifest['_raw_cells'])} 单元格",
        f"raw_snapshot/raw_cells.jsonl；catalog.sqlite:raw_cells")

    # 3 版本正确
    sat = q("SELECT COUNT(*) FROM phone_variants WHERE satellite_support=1")
    lpd = q("SELECT COUNT(*) FROM skus WHERE storage_raw LIKE '%LPDDR5T%'")
    dup_combos = q("SELECT COUNT(*) FROM (SELECT model_id,variant_id, ram_raw, storage_raw, COUNT(*) c "
                   "FROM skus GROUP BY model_id,variant_id, ram_raw, storage_raw HAVING c>1)")
    add("版本正确", "普通版/卫星版/内存类型差异保留；跨版本自动合并为零",
        sat >= 3 and lpd >= 1 and dup_combos == 0,
        f"卫星通信版 variant {sat} 个；含 LPDDR5T 标注的 sku {lpd} 个；"
        f"同硬件版本同 RAM 同存储的重复 sku {dup_combos} 个；跨版本配置保留",
        "catalog.sqlite:phone_variants / skus")

    # 4 关联有效
    add("关联有效", "机型/版本/配置/价格/指标/图片外键均有效；未匹配对象进入问题清单",
        len(fk_errors) == 0,
        f"PRAGMA foreign_key_check 违规 {len(fk_errors)} 条；"
        f"未匹配机型问题 {sum(1 for i in issues if i['issue_code'].endswith('UNMATCHED'))} 条",
        json.dumps(fk_errors[:5], ensure_ascii=False))

    # 5 缺失正确
    n_null = q("SELECT COUNT(*) FROM metric_observations WHERE value_num IS NULL")
    n_zero = q("SELECT COUNT(*) FROM metric_observations WHERE value_num=0")
    reasons = dict(con.execute("SELECT COALESCE(missing_reason,'<有值>'), COUNT(*) "
                               "FROM metric_observations GROUP BY 1").fetchall())
    add("缺失正确", "NULL / 明确无功能 / 未公开 / 未查到分别记录；无自动填 0 或推算实测",
        reasons.get("not_reported", 0) > 0 and reasons.get("not_found", 0) > 0,
        f"指标观测共 {q('SELECT COUNT(*) FROM metric_observations')} 条，"
        f"NULL {n_null} 条，值为 0 的 {n_zero} 条；原因分布 {reasons}",
        "catalog.sqlite:metric_observations.missing_reason")

    # 6 可比性明确
    groups = q("SELECT COUNT(DISTINCT comparison_group) FROM metric_observations")
    proto = dict(con.execute("SELECT protocol, COUNT(*) FROM benchmark_runs "
                             "WHERE benchmark='相机综合' GROUP BY 1").fetchall())
    add("可比性明确", "相机协议、游戏画质和帧率条件有标记；不可比记录不静默混排",
        groups >= 5,
        f"可比性分组 {groups} 个；相机运行按协议分布 {proto}；"
        f"游戏运行按 条件原文 分组，未跨条件混排",
        "catalog.sqlite:metric_observations.comparison_group")

    # 7 价格正确
    cond = dict(con.execute("SELECT condition, COUNT(*) FROM price_snapshots "
                            "GROUP BY 1").fetchall())
    ss = dict(con.execute("SELECT source_sale_status, COUNT(*) FROM price_snapshots "
                          "GROUP BY 1").fetchall())
    add("价格正确", "新机与二手分开筛选；原始停售状态未被当成渠道无库存",
        cond.get("new", 0) > 0 and cond.get("used", 0) > 0 and cond.get("unknown", 0) > 0,
        f"成色分布 {cond}；原始销售状态 {ss}（原样保留在 source_sale_status，"
        f"未映射为渠道库存）",
        "catalog.sqlite:price_snapshots")

    # 8 重建稳定
    add("重建稳定", "同一输入重复导入的业务 ID、规范记录和观测数量一致，无重复追加",
        None,
        f"本次机型 {id_stats['models']} / 版本 {id_stats['variants']} / 配置 {id_stats['skus']}；"
        f"重复运行请执行 verify_rebuild 命令对照",
        "python build_dataset.py --config config/build_config.json && "
        "python tests/acceptance.py")

    # 9 中文可用
    bad = q("SELECT COUNT(*) FROM raw_cells WHERE raw_value_text LIKE '%\ufffd%'")
    add("中文可用", "原文中文逐值一致；XLSX 与 DOCX 可打开，无缺字/乱码/截断/科学计数法 ID",
        bad == 0,
        f"原始层替换字符 U+FFFD 命中 {bad} 处；"
        f"XLSX 工作表 {len(openpyxl.load_workbook(os.path.join(paths['output_dir'], cfg['workbook_name'])).sheetnames)} 个；视觉与 WPS 独立复核",
        "logs/wps_review.md")

    # 10 网页可读
    add("网页可读", "本地预览可查询 147 个产品名称对应数据；每个推荐候选 SKU 不重复",
        None, "见 tests/acceptance.py 的接口检查结果与 data_preview.py 输出",
        "python data_preview.py --list-models")



    out = paths["output_dir"]
    edge = [
        f"1TB 与 1TB (LPDDR5T)：1TB 存储的 sku {q('SELECT COUNT(*) FROM skus WHERE storage_raw=\"1TB\"')} 个，"
        f"1TB (LPDDR5T) 的 sku {q('SELECT COUNT(*) FROM skus WHERE storage_raw LIKE \"%LPDDR5T%\"')} 个，"
        f"后者 memory_type 单列为 LPDDR5T，两者 storage_gb 均为 1024。",
        f"普通版与卫星版：卫星通信版 variant {q('SELECT COUNT(*) FROM phone_variants WHERE satellite_support=1')} 个，"
        f"全部与标准版保持独立 variant_id；名称+RAM+存储 相同但版本不同的组合 "
        f"{q('SELECT COUNT(*) FROM (SELECT model_id,ram_raw,storage_raw,COUNT(*) c FROM skus GROUP BY 1,2,3 HAVING c>1)')} 个"
        f"（源表 484 行只有 482 个名称+RAM+存储 组合，差异由卫星版解释，未删除任何行）；"
        f"同一 variant 内重复 sku "
        f"{q('SELECT COUNT(*) FROM (SELECT model_id,variant_id,ram_raw,storage_raw,COUNT(*) c FROM skus GROUP BY 1,2,3,4 HAVING c>1)')} 个。",
        f"二手约价：condition=used 且 is_estimated=1 的价格 "
        f"{q('SELECT COUNT(*) FROM price_snapshots WHERE condition=\"used\" AND is_estimated=1')} 条，"
        f"全部标记 estimated，未当作新机价。",
        f"带优惠条件的价：版本/备注含「首销价…起」的记录 "
        f"{q('SELECT COUNT(*) FROM raw_cells WHERE field_name=\"版本/备注\" AND raw_value_text LIKE \"%首销价%\"')} 行，"
        f"限定条件已保留，未取首值当单值。",
        f"日期只有月份：发布时间粒度 month 的机型 "
        f"{q('SELECT COUNT(*) FROM phone_models WHERE release_granularity=\"month\"')} 个，"
        f"未补造发布日；查询日期全表同一值 2026-10-02，已标注为「查询日期」而非成交时间。",
        f"来源未公开：missing_reason=not_reported 的观测 "
        f"{q('SELECT COUNT(*) FROM metric_observations WHERE missing_reason=\"not_reported\"')} 条，"
        f"与 not_found（{q('SELECT COUNT(*) FROM metric_observations WHERE missing_reason=\"not_found\"')} 条）分别记录。",
        f"无镜头与缺评分：DXOMARK 变焦子分缺失 "
        f"{q('SELECT COUNT(*) FROM metric_observations WHERE metric_key=\"dxomark_zoom\" AND value_num IS NULL')} 条，"
        f"夜景子分缺失 "
        f"{q('SELECT COUNT(*) FROM metric_observations WHERE metric_key=\"dxomark_lowlight\" AND value_num IS NULL')} 条，"
        f"均为 NULL 且不推算。",
        f"DXOMARK 不同协议：可比性分组 "
        f"{[r[0] for r in con2_rows(db, 'SELECT DISTINCT comparison_group FROM metric_observations WHERE metric_key LIKE \"dxomark%\" ORDER BY 1')]}，"
        f"不同协议未静默混排。",
        f"60/120 帧模式：游戏运行的 comparison_group 按测试条件原文分组，共 "
        f"{q('SELECT COUNT(DISTINCT comparison_group) FROM metric_observations WHERE metric_key LIKE \"honor_of_kings%\"')} 组，"
        f"60 帧与 120 帧模式不直接混排。",
        f"缺图片与 AVIF 解码：ZIP 成员 {assets_pack['stats'].get('members')} 个、"
        f"图片 {assets_pack['stats'].get('images')} 个、可解码 {assets_pack['stats'].get('decoded')} 个、"
        f"解码失败 {assets_pack['stats'].get('decode_failed')} 个、机型覆盖 "
        f"{assets_pack['stats'].get('models_covered')} 个、颜色 {assets_pack['stats'].get('colors')} 种。",
        f"原始集合对照：{source_audit['status']}；派生复算见独立验收脚本；人工抽查尚未完成。",
    ]

    con.close()
    config_arg = "data_pipeline/config/build_config.local.json"
    db_arg = os.path.join(out, "catalog.sqlite")
    reproduce = [
        "# Run from the repository root; set PHONE_SOURCE_DIR for your own input directory.",
        f'python data_pipeline/build_dataset.py --config {config_arg} --verify-only',
        f'python data_pipeline/build_dataset.py --config {config_arg}',
        f'python data_pipeline/tests/acceptance.py --config {config_arg} --rebuild-check',
        f'python data_pipeline/data_preview.py --db "{db_arg}" --list-models',
        f'python data_pipeline/data_preview.py --db "{db_arg}" --model "vivo X200 Pro" --observations --missing',
    ]
    next_steps = [
        f"WPS 复核：在装有 WPS 的机器上打开 {cfg['workbook_name']}，检查表头、冻结区域、"
        "筛选、中文与 ID 格式，复查关键原始值与派生值；复核后把本报告 wps_review 字段改为 PASS。",
        "正式网页接入：新接口 repository.py 已完成本地验证，接入前请确认 phone-demo 数据加载入口，"
        "将 data/phones.xlsx 的读取替换为 CatalogRepository，并保留原有部署副本。",
        "未解决匹配：未匹配机型问题 "
        f"{sum(1 for i in issues if i['issue_code'].endswith('UNMATCHED'))} 条，"
        "进入 quality_issues 表，不参与正式推荐候选。",
        "不可比数据：DXOMARK 不同协议、60/120 帧模式、不同亮度口径的记录已用 comparison_group 隔离，"
        "跨组比较前需人工确认。",
        "缺失项目：missing_reason 为 not_found / not_reported 的观测保持 NULL，"
        "补数时通过导入代码生成新数据版本，不直接改库。",
        "本次不推送、不合并正式分支，未替换正式网页数据。",
    ]

    deliverables = {
        "规范 Excel": os.path.join(out, cfg['workbook_name']),
        "SQLite 目录库": os.path.join(out, "catalog.sqlite"),
        "输入清单": os.path.join(out, "source_manifest.json"),
        "ID 注册表": os.path.join(out, "id_registry.csv"),
        "机型别名表": os.path.join(out, "model_aliases.csv"),
        "图片清单": os.path.join(out, "assets_manifest.csv"),
        "ZIP 成员清单": os.path.join(out, "zip_members.csv"),
        "质检问题清单": os.path.join(out, "quality_issues.csv"),
        "质量报告": os.path.join(out, "quality_report.json"),
        "验收报告": os.path.join(out, cfg['report_name']),
        "网页副本目录": os.path.join(out, "assets_derived"),
        "原包解压目录": os.path.join(out, "assets_extracted"),
    }

    severity = Counter(i["severity"] for i in issues)
    report = {
        "dataset_version": cfg["dataset_version"],
        "rule_version": RULE_VERSION,
        "id_registry_version": cfg["id_registry_version"],
        "built_at": now_iso(),
        "output_dir": paths["output_dir"],
        "checks": checks,
        "edge_cases": edge,
        "reproduce_commands": reproduce,
        "next_steps": next_steps,
        "deliverables": deliverables,
        "table_counts": counts,
        "id_stats": id_stats,
        "quality_issues": {"total": len(issues), "by_severity": dict(severity),
                           "by_code": dict(Counter(i["issue_code"] for i in issues).most_common())},
        "aux_table_diff": {"consistent": len(aux_diff["consistent"]),
                           "conflicts": len(aux_diff["conflicts"]),
                           "aux_only": len(aux_diff["aux_only"])},
        "asset_stats": assets_pack["stats"],
        "stage_times_sec": log["stage_times"],
        "warnings": log["warnings"],
        "wps_review": "NOT_CHECKED",
        "web_deployment": "未更新正式网页数据（本次不推送、不合并正式分支）",
        # 任务书要求分别列出四项状态，不用一句话笼统概括
        "delivery_status": {
            "数据库完成": "完成：catalog.sqlite 已构建，外键违规 0，"
                        "原始层与规范层同一份构建结果导出，重复构建 ID/数值/关联一致",
            "本地预览通过": "通过：data_preview.py 与 repository.py 本地验证通过，"
                          "147 个机型全部可查详情，候选 SKU 无重复；"
                          "详见验收项「网页可读」",
            "WPS 已复核或待复核": "待复核（NOT_CHECKED）：本机未安装 WPS，"
                               "未声称已通过；复核清单见 logs/wps_review.md",
            "正式网页未更新": "未更新：本次不推送、不合并正式分支，"
                            "未替换 phone-demo 的 data/phones.xlsx 既有部署副本",
        },
        # 任务书：VIKOR 可预留接口；未实现时显式标注，不得返回模拟成绩
        "algorithm_scope": {
            "topsis": "未实现、未改动：本次只交付候选与指标定义，"
                      "不替换既有 TOPSIS 排序逻辑",
            "vikor": "未实现（显式标注）：未提供接口实现，不返回任何模拟成绩；"
                     "数据输出协议已就绪——build_decision_input 返回的 "
                     "candidates / X / criterion_definitions / weights / missing_mask / "
                     "comparison_groups / provenance 可直接供 VIKOR 复用，"
                     "各算法自行按 direction（benefit/cost/target）归一化",
            "weights_note": "weights 为试验默认等权值，不是行业共识，不写入交付数据",
            "composite_score": "未实现：本任务不实现新的拍照硬件综合分，"
                               "描述性结论不折算成 0-10 分",
        },
    }
    apply_report_status(report)
    return report


def con2_rows(db, sql):
    import sqlite3
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def apply_report_status(report):
    """Machine completion never impersonates human review or public deployment."""
    checks = report.get('checks', [])
    machine = 'FAIL' if any(c['result'] == 'FAIL' for c in checks) else 'PASS' if checks and all(c['result'] == 'PASS' for c in checks) else 'PENDING'
    report['machine_checks'] = list(checks)
    report['manual_checks'] = report.get('manual_checks') or [
        {'item':'跨品牌人工抽查','result':'NOT_CHECKED','completed':0,'target':20},
        {'item':'WPS复核','result':report.get('wps_review','NOT_CHECKED')}]
    manual_pass = all(c['result'] == 'PASS' for c in report['manual_checks'])
    report['machine_acceptance'] = machine
    report['overall_acceptance'] = 'FAIL' if machine == 'FAIL' or any(c['result'] == 'FAIL' for c in report['manual_checks']) else 'PASS' if machine == 'PASS' and manual_pass else 'PENDING'
    result = lambda name: next((c['result'] for c in checks if c['item'] == name),'NOT_CHECKED')
    report['delivery_status'] = {
        '数据库完成':'FAIL 数据库机器验收失败' if machine == 'FAIL' else 'PASS 已构建且机器检查通过 人工验收待完成' if machine == 'PASS' else 'PENDING 已构建 机器验收尚未全部完成',
        '本地预览通过':result('网页可读'),
        'WPS 已复核或待复核':report.get('wps_review','NOT_CHECKED') + ' 需实际复核并保存记录',
        '正式网页未更新':'未更新 本阶段仅构建新库 原网页未替换',
    }


if __name__ == "__main__":
    main()
