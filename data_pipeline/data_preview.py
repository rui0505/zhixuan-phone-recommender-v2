# -*- coding: utf-8 -*-
"""本地数据预览入口（data_preview.py）。

用途：在接入正式网页前，按机型查看配置、切换新机/二手价格、查看评测来源与图片、
列出缺失项目。只读目录库，不修改任何数据。

示例：
  python data_preview.py --list-models
  python data_preview.py --model "iPhone 16 Pro" --mode used
  python data_preview.py --model M0003 --observations --assets
  python data_preview.py --model "vivo X200 Pro" --missing
  python data_preview.py --decision --metrics honor_of_kings_avg_fps,dxomark_photo --mode new
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from repository import CatalogRepository  # noqa: E402


def _print_table(rows, cols=None, limit=None, width=28):
    if not rows:
        print("  （无记录）")
        return
    cols = cols or list(rows[0].keys())
    shown = rows[:limit] if limit else rows
    print("  " + " | ".join(c[:14].ljust(min(len(c), 14)) for c in cols))
    print("  " + "-" * (17 * len(cols)))
    for r in shown:
        vals = []
        for c in cols:
            v = r.get(c)
            s = "" if v is None else str(v)
            if len(s) > width:
                s = s[:width - 1] + "…"
            vals.append(s.ljust(min(len(c), 14)))
        print("  " + " | ".join(vals))
    if limit and len(rows) > limit:
        print(f"  … 其余 {len(rows) - limit} 条省略")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="catalog.sqlite 路径")
    ap.add_argument("--model", default=None, help="机型名称或 model_id")
    ap.add_argument("--mode", default="all", choices=["all", "new", "used"],
                    help="价格模式：新机/二手/全部")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--observations", action="store_true", help="显示评测指标与来源")
    ap.add_argument("--assets", action="store_true", help="显示图片资源")
    ap.add_argument("--specs", action="store_true", help="显示规格参数")
    ap.add_argument("--missing", action="store_true", help="只显示缺失项目")
    ap.add_argument("--snippets", action="store_true", help="显示评测摘录与检索标签")
    ap.add_argument("--search", default=None, help="在摘录原文与标签中做关键词检索")
    ap.add_argument("--tag", default=None, help="按检索标签过滤摘录，如 夜景")
    ap.add_argument("--decision", action="store_true", help="预览决策输入")
    ap.add_argument("--metrics", default="honor_of_kings_avg_fps,dxomark_photo",
                    help="决策输入使用的指标，逗号分隔")
    ap.add_argument("--max-price", type=float, default=None)
    ap.add_argument("--min-storage", type=float, default=None)
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    repo = CatalogRepository(args.db)
    ds = repo.dataset_version()
    print(f"数据集版本：{ds.get('dataset_version')}　规则版本：{ds.get('rule_version')}　"
          f"构建时间：{ds.get('built_at')}")
    print(f"目录库：{repo.db_path}")

    if args.list_models:
        rows = repo.as_records(repo.list_models())
        print(f"\n机型总数：{len(rows)}")
        _print_table(rows, limit=None)
        return

    if args.decision:
        keys = [k.strip() for k in args.metrics.split(",") if k.strip()]
        di = repo.build_decision_input(metric_keys=keys, mode=args.mode,
                                       max_price=args.max_price,
                                       min_storage_gb=args.min_storage)
        cands = repo.as_records(di["candidates"])
        print(f"\n候选数：{len(cands)}（一行一个 sku_id，模式 {args.mode}）")
        print("指标定义：")
        for c in di["criterion_definitions"]:
            print(f"  {c['metric_key']}（{c['name_cn']}，{c['unit']}，{c['direction']}）"
                  f"　可比组={c['comparison_group_used']}　覆盖={c['comparison_group_coverage']}")
        print(f"权重（试验默认值）：{di['weights']}")
        _print_table(cands, cols=["sku_id", "display_name", "variant_name", "ram_raw",
                                  "storage_raw", "memory_type", "price_cny", "condition",
                                  "price_kind"], limit=20)
        n_missing = sum(sum(1 for m in row if m) for row in di["missing_mask"])
        print(f"\n缺失单元数：{n_missing} / {len(cands) * len(keys)}")
        if args.json:
            print(json.dumps({"criterion_definitions": di["criterion_definitions"],
                              "weights": di["weights"],
                              "missing_count": n_missing,
                              "dataset_version": di["dataset_version"]},
                             ensure_ascii=False, indent=1))
        return

    if not args.model:
        ap.error("请指定 --model，或使用 --list-models / --decision")

    det = repo.get_model_details(model_name=args.model, mode=args.mode)
    if not det or not det["model"]:
        print(f"未找到机型：{args.model}")
        return
    m = det["model"]
    print(f"\n=== {m['display_name']}（{m['model_id']}，{m['brand']}）===")
    print(f"发布：{m['release_month_raw']} -> {m['release_month']}（粒度 {m['release_granularity']}）")
    print(f"封面图片：{m['cover_asset_id']}")

    print("\n-- 硬件版本 --")
    _print_table(det["variants"], cols=["variant_id", "variant_name", "region",
                                        "satellite_support", "is_default", "variant_note_raw"])

    print(f"\n-- 购买配置与价格（模式 {args.mode}）--")
    price_by_sku = {}
    for p in det["prices"]:
        price_by_sku.setdefault(p["sku_id"], p)
    rows = []
    for s in det["skus"]:
        p = price_by_sku.get(s["sku_id"], {})
        rows.append({
            "sku_id": s["sku_id"], "运行内存": s["ram_raw"], "存储": s["storage_raw"],
            "内存类型": s["memory_type"], "价格原文": p.get("price_raw"),
            "price_cny": p.get("amount"), "成色": p.get("condition"),
            "价格类型": p.get("price_kind"), "估计值": p.get("is_estimated"),
            "销售状态": p.get("source_sale_status"), "采集日期": p.get("observed_at"),
        })
    _print_table(rows, limit=None)

    if args.specs:
        print("\n-- 规格参数 --")
        _print_table(det["specifications"],
                     cols=["spec_key", "value_num", "value_text", "unit", "raw_value",
                           "parse_status", "comparison_group", "note"], limit=40)

    if args.observations:
        print("\n-- 评测运行与指标 --")
        for run in det["benchmark_runs"]:
            print(f"  [{run['run_id']}] {run['benchmark']} / {run['institution']} / "
                  f"{run['protocol']}　日期={run['run_date']}　可比组={run['comparison_group']}")
            print(f"      范围={run['applicable_scope']}　{run['scope_note']}")
            for o in [x for x in det["observations"] if x["run_id"] == run["run_id"]]:
                val = o["value_num"] if o["value_num"] is not None else "NULL"
                flag = "" if o["value_num"] is not None else f"（{o['missing_reason']}）"
                print(f"      - {o['metric_key']}: {val} {o['unit']} {flag}"
                      f"　原文={o['raw_value']!r}　源记录={o['source_record_id']}")

    if args.assets:
        print("\n-- 图片资源 --")
        _print_table(repo.as_records(det["assets"]),
                     cols=["asset_id", "color_id", "color_name", "format",
                           "width", "height", "decode_status", "derived_path",
                           "is_cover"], limit=30)

    if args.missing:
        print("\n-- 缺失项目 --")
        miss = det["missing"]
        if not miss:
            print("  （无缺失指标）")
        for o in miss:
            print(f"  {o['metric_key']}: NULL　原因={o['missing_reason']}　"
                  f"原文={o['raw_value']!r}　源记录={o['source_record_id']}")
        no_price = [s for s in det["skus"] if s["sku_id"] not in price_by_sku]
        if no_price:
            print(f"  无价格的配置：{[s['sku_id'] for s in no_price]}")
        if len(repo.as_records(det["assets"])) == 0:
            print("  该机型无图片，网页显示文字占位")

    if args.snippets:
        print("\n-- 评测摘录与检索标签 --")
        rows = repo.as_records(repo.get_evidence_snippets(mid, tag=args.tag))
        if not rows:
            print("  （无摘录）")
        for s in rows:
            print(f"  [{s['snippet_kind']}] {s['snippet_text'][:110]}")
            print(f"      标签={s['tags'] or '（无）'}（{s['tag_method']}）　"
                  f"内容来源={s['origin']}　支持指标={s['related_metric_keys'] or '—'}　"
                  f"源记录={s['source_record_id']}")

    if args.search:
        print(f"\n-- 关键词检索：{args.search} --")
        hits = repo.as_records(repo.search_text(args.search))
        if not hits:
            print("  （无命中）")
        for h in hits:
            print(f"  {h['snippet_id']} {h['subject_id']} [{h['snippet_kind']}] "
                  f"{h['snippet_text'][:90]}　标签={h['tags']}")

    if args.json:
        print(json.dumps({"model": det["model"], "variants": det["variants"],
                          "sku_count": len(det["skus"]),
                          "observation_count": len(det["observations"]),
                          "asset_count": len(repo.as_records(det["assets"])),
                          "snippet_count": len(det["evidence_snippets"])},
                         ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
