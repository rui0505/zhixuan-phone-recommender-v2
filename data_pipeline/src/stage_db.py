# -*- coding: utf-8 -*-
"""阶段五：SQLite 目录库构建。

幂等策略：每次构建在临时文件上重建全部表，成功后原子替换 catalog.sqlite。
业务 ID 来自持久化的 id_registry.csv，因此重复运行不会重复追加价格或评测记录。
"""
from __future__ import annotations

import json
import os
import sqlite3

from common import now_iso
from rules import FIELD_DICTIONARY, METRIC_DEFINITIONS, NON_COMPARABLE_METRICS, RULE_VERSION

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE ingestion_files (
  file_key TEXT PRIMARY KEY, file_name TEXT, abs_path TEXT, role TEXT,
  sha256_before TEXT, sha256_after TEXT, size_bytes INTEGER, mtime TEXT,
  sheets_json TEXT, row_count INTEGER, col_count INTEGER,
  encoding_scan_json TEXT, verified_unchanged INTEGER
);

CREATE TABLE raw_rows (
  raw_row_pk INTEGER PRIMARY KEY AUTOINCREMENT,
  source_record_id TEXT NOT NULL, file_key TEXT NOT NULL, sheet_name TEXT NOT NULL,
  source_row_no INTEGER NOT NULL, is_header_row INTEGER NOT NULL,
  values_json TEXT NOT NULL, value_types_json TEXT NOT NULL
);
CREATE INDEX idx_raw_rows_src ON raw_rows(source_record_id);
CREATE INDEX idx_raw_rows_file ON raw_rows(file_key, sheet_name, source_row_no);

CREATE TABLE raw_cells (
  raw_cell_pk INTEGER PRIMARY KEY AUTOINCREMENT,
  source_record_id TEXT NOT NULL, file_key TEXT NOT NULL, sheet_name TEXT NOT NULL,
  source_row_no INTEGER NOT NULL, source_col_no INTEGER NOT NULL, col_letter TEXT,
  field_name TEXT, raw_value_text TEXT, raw_value_type TEXT,
  formula TEXT, cached_value_text TEXT, cached_value_type TEXT, number_format TEXT
);
CREATE INDEX idx_raw_cells_src ON raw_cells(source_record_id);
CREATE INDEX idx_raw_cells_field ON raw_cells(file_key, sheet_name, field_name);

CREATE TABLE phone_models (
  model_id TEXT PRIMARY KEY, brand TEXT, display_name TEXT NOT NULL,
  display_name_match_key TEXT NOT NULL,
  release_month TEXT, release_month_raw TEXT, release_granularity TEXT,
  first_source_record_id TEXT, cover_asset_id TEXT, note TEXT
);

CREATE TABLE phone_variants (
  variant_id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL REFERENCES phone_models(model_id),
  variant_name TEXT NOT NULL, name_is_source INTEGER NOT NULL,
  region TEXT, region_evidence TEXT, satellite_support INTEGER,
  is_default INTEGER, variant_note_raw TEXT, source_record_id TEXT
);

CREATE TABLE skus (
  sku_id TEXT PRIMARY KEY,
  variant_id TEXT NOT NULL REFERENCES phone_variants(variant_id),
  model_id TEXT NOT NULL REFERENCES phone_models(model_id),
  ram_raw TEXT, ram_gb REAL, storage_raw TEXT, storage_gb REAL,
  memory_type TEXT, memory_type_source TEXT, source_record_id TEXT
);

CREATE TABLE price_snapshots (
  price_id TEXT PRIMARY KEY,
  sku_id TEXT NOT NULL REFERENCES skus(sku_id),
  amount REAL, currency TEXT, price_raw TEXT, price_kind TEXT, condition TEXT,
  is_estimated INTEGER, channel TEXT, seller TEXT, price_note TEXT, qualifiers TEXT,
  source_sale_status TEXT, observed_at TEXT, observed_at_kind TEXT,
  source_id TEXT, source_record_id TEXT
);

CREATE TABLE specifications (
  spec_id TEXT PRIMARY KEY, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
  variant_id TEXT, spec_key TEXT NOT NULL, unit TEXT,
  value_num REAL, value_text TEXT, raw_value TEXT, raw_value_type TEXT,
  source_col TEXT, source_record_id TEXT, source_id TEXT,
  parse_status TEXT, note TEXT, comparison_group TEXT, rule_version TEXT
);
CREATE INDEX idx_spec_subject ON specifications(subject_type, subject_id, spec_key);

CREATE TABLE benchmark_runs (
  run_id TEXT PRIMARY KEY, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
  model_id TEXT, variant_id TEXT, sku_id TEXT,
  benchmark TEXT, institution TEXT, protocol TEXT, protocol_detail TEXT,
  conditions_json TEXT, run_date TEXT, os_version TEXT,
  comparison_group TEXT, applicable_scope TEXT, scope_note TEXT,
  source_id TEXT, source_record_id TEXT, data_note TEXT, parse_note TEXT,
  FOREIGN KEY (model_id) REFERENCES phone_models(model_id),
  FOREIGN KEY (variant_id) REFERENCES phone_variants(variant_id),
  FOREIGN KEY (sku_id) REFERENCES skus(sku_id)
);
CREATE INDEX idx_runs_subject ON benchmark_runs(subject_type, subject_id);

CREATE TABLE metric_observations (
  observation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES benchmark_runs(run_id),
  metric_key TEXT NOT NULL, value_num REAL, unit TEXT,
  raw_value TEXT, raw_value_type TEXT, missing_reason TEXT,
  source_col TEXT, source_record_id TEXT, comparison_group TEXT,
  verification_status TEXT, note TEXT
);
CREATE INDEX idx_obs_run ON metric_observations(run_id);
CREATE INDEX idx_obs_metric ON metric_observations(metric_key);

CREATE TABLE metric_definitions (
  metric_key TEXT PRIMARY KEY, name_cn TEXT, unit TEXT, direction TEXT,
  comparability_prefix TEXT, value_type TEXT, range_rule TEXT,
  cross_source_comparable INTEGER, note TEXT
);

CREATE TABLE evidence_snippets (
  snippet_id TEXT PRIMARY KEY,
  source_id TEXT REFERENCES sources(source_id),
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
  related_run_id TEXT, related_metric_keys TEXT,
  snippet_kind TEXT, snippet_text TEXT NOT NULL,
  source_col TEXT, source_ref_col TEXT, source_record_id TEXT,
  origin TEXT, extraction_method TEXT, tag_method TEXT,
  tags TEXT, tag_count INTEGER, note TEXT
);
CREATE INDEX idx_snippet_subject ON evidence_snippets(subject_type, subject_id);
CREATE INDEX idx_snippet_kind ON evidence_snippets(snippet_kind);

CREATE TABLE sources (
  source_id TEXT PRIMARY KEY, source_kind TEXT, raw_text TEXT,
  url TEXT, url_count INTEGER, publisher TEXT,
  published_at TEXT, published_granularity TEXT, collected_at TEXT,
  url_is_parseable INTEGER
);

CREATE TABLE assets (
  asset_id TEXT PRIMARY KEY,
  model_id TEXT REFERENCES phone_models(model_id),
  variant_id TEXT, color_id TEXT, color_name TEXT, applicable_scope TEXT,
  original_zip_path TEXT, extracted_path TEXT, derived_path TEXT, asset_key TEXT,
  format TEXT, width INTEGER, height INTEGER, size_bytes INTEGER, sha256 TEXT,
  decode_status TEXT, index_declared_sha256 TEXT, sha256_match INTEGER,
  index_width INTEGER, index_height INTEGER, dimension_match INTEGER,
  source_kind TEXT, source_url TEXT, source_page TEXT, is_cover INTEGER, note TEXT
);
CREATE INDEX idx_assets_model ON assets(model_id);

CREATE TABLE asset_colors (
  color_id TEXT PRIMARY KEY,
  model_id TEXT REFERENCES phone_models(model_id),
  color_name TEXT
);

CREATE TABLE model_covers (
  model_id TEXT PRIMARY KEY REFERENCES phone_models(model_id),
  cover_asset_id TEXT, cover_rule TEXT, color_name TEXT
);

CREATE TABLE zip_members (
  member_path TEXT PRIMARY KEY, is_dir INTEGER, is_image INTEGER, format TEXT,
  size_bytes INTEGER, compress_size INTEGER, crc32 TEXT, sha256 TEXT,
  path_safe INTEGER, extracted INTEGER, extract_skip_reason TEXT
);

CREATE TABLE import_mapping (
  mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file_key TEXT NOT NULL, sheet_name TEXT, source_row_no INTEGER,
  source_record_id TEXT NOT NULL,
  target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  mapping_role TEXT, merge_note TEXT
);
CREATE INDEX idx_mapping_src ON import_mapping(source_record_id);
CREATE INDEX idx_mapping_target ON import_mapping(target_type, target_id);

CREATE TABLE dataset_releases (
  release_id TEXT PRIMARY KEY, dataset_version TEXT, rule_version TEXT,
  id_registry_version TEXT, input_hashes_json TEXT, built_at TEXT,
  table_counts_json TEXT, notes TEXT
);

CREATE TABLE quality_issues (
  issue_id INTEGER PRIMARY KEY AUTOINCREMENT, issue_code TEXT NOT NULL,
  severity TEXT NOT NULL, source_record_id TEXT, detail TEXT,
  detected_at TEXT, status TEXT
);

CREATE TABLE field_dictionary (
  field_name TEXT PRIMARY KEY, layer TEXT, unit TEXT, description TEXT
);
"""

TABLES = ["ingestion_files", "raw_rows", "raw_cells", "phone_models", "phone_variants",
          "skus", "price_snapshots", "specifications", "benchmark_runs",
          "metric_observations", "metric_definitions", "sources", "evidence_snippets",
          "assets", "asset_colors", "model_covers", "zip_members", "import_mapping",
          "dataset_releases", "quality_issues", "field_dictionary"]


def ph(cols) -> str:
    """按列数生成 SQLite 占位符串，避免手工数错列数。"""
    return ",".join("?" * len(cols))


def build_db(db_path, manifest, entities, assets_pack, issues, stats, cfg):
    tmp = db_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(DDL)
        con.execute("BEGIN")

        # 输入文件
        for f in manifest["files"]:
            con.execute(
                "INSERT INTO ingestion_files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f["file_key"], f["file_name"], f["abs_path"], f["role"],
                 f.get("sha256_before", ""), f.get("sha256_after", ""),
                 f.get("size_bytes"), f.get("mtime"),
                 json.dumps(f.get("sheets", []), ensure_ascii=False),
                 f.get("row_count"), f.get("col_count"),
                 json.dumps(f.get("encoding_scan", {}), ensure_ascii=False),
                 1 if f.get("sha256_after") == f.get("sha256_before") and f.get("sha256_before") else None))

        # 原始层
        for r in manifest["_raw_rows"]:
            con.execute(
                "INSERT INTO raw_rows (source_record_id, file_key, sheet_name, source_row_no,"
                " is_header_row, values_json, value_types_json) VALUES (?,?,?,?,?,?,?)",
                (r["source_record_id"], r["file_key"], r["sheet_name"], r["source_row_no"],
                 r["is_header_row"], json.dumps(r["values_text"], ensure_ascii=False),
                 json.dumps(r["value_types"], ensure_ascii=False)))
        for c in manifest["_raw_cells"]:
            con.execute(
                "INSERT INTO raw_cells (source_record_id, file_key, sheet_name, source_row_no,"
                " source_col_no, col_letter, field_name, raw_value_text, raw_value_type, formula,"
                " cached_value_text, cached_value_type, number_format) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (c["source_record_id"], c["file_key"], c["sheet_name"], c["source_row_no"],
                 c["source_col_no"], c["col_letter"], c["field_name"], c["raw_value_text"],
                 c["raw_value_type"], c["formula"], c["cached_value_text"],
                 c["cached_value_type"], c["number_format"]))

        # 规范层
        con.executemany(f"INSERT INTO phone_models VALUES ({ph(MODEL_COLS)})",
                        [tuple(m[h] for h in MODEL_COLS) for m in entities["phone_models"]])
        con.executemany(f"INSERT INTO phone_variants VALUES ({ph(VARIANT_COLS)})",
                        [tuple(v[h] for h in VARIANT_COLS) for v in entities["phone_variants"]])
        con.executemany(f"INSERT INTO skus VALUES ({ph(SKU_COLS)})",
                        [tuple(s[h] for h in SKU_COLS) for s in entities["skus"]])
        con.executemany(f"INSERT INTO price_snapshots VALUES ({ph(PRICE_COLS)})",
                        [tuple(p[h] for h in PRICE_COLS) for p in entities["price_snapshots"]])
        con.executemany(f"INSERT INTO specifications VALUES ({ph(SPEC_COLS)})",
                        [tuple(s[h] for h in SPEC_COLS) for s in entities["specifications"]])
        con.executemany(f"INSERT INTO benchmark_runs VALUES ({ph(RUN_COLS)})",
                        [tuple(r[h] for h in RUN_COLS) for r in entities["benchmark_runs"]])
        con.executemany(f"INSERT INTO metric_observations VALUES ({ph(OBS_COLS)})",
                        [tuple(o[h] for h in OBS_COLS) for o in entities["metric_observations"]])
        con.executemany(f"INSERT INTO sources VALUES ({ph(SOURCE_COLS)})",
                        [tuple(s[h] for h in SOURCE_COLS) for s in entities["sources"]])
        con.executemany(f"INSERT INTO evidence_snippets VALUES ({ph(SNIPPET_COLS)})",
                        [tuple(s.get(h) for h in SNIPPET_COLS)
                         for s in entities.get("evidence_snippets", [])])

        # 指标定义
        for key, name_cn, unit, direction, prefix, vtype, rng in METRIC_DEFINITIONS:
            con.execute("INSERT INTO metric_definitions VALUES (?,?,?,?,?,?,?,?,?)",
                        (key, name_cn, unit, direction, prefix, vtype, rng,
                         0 if key in NON_COMPARABLE_METRICS else 1,
                         "源数据自带归一化，不作为跨协议统一排名指标"
                         if key in NON_COMPARABLE_METRICS else ""))

        # 资源
        con.executemany(f"INSERT INTO assets VALUES ({ph(ASSET_COLS)})",
                        [tuple(a[h] for h in ASSET_COLS) for a in assets_pack["assets"]])
        seen_colors = {}
        for a in assets_pack["assets"]:
            if a["color_id"] and a["color_id"] not in seen_colors:
                seen_colors[a["color_id"]] = (a["color_id"], a["model_id"], a["color_name"])
        con.executemany("INSERT INTO asset_colors VALUES (?,?,?)", list(seen_colors.values()))
        con.executemany(f"INSERT INTO model_covers VALUES ({ph(COVER_COLS)})",
                        [tuple(c[h] for h in COVER_COLS) for c in assets_pack["covers"]])
        con.executemany(f"INSERT INTO zip_members VALUES ({ph(MEMBER_COLS)})",
                        [tuple(m[h] for h in MEMBER_COLS) for m in assets_pack["members"]])

        # 映射与问题
        con.executemany(
            "INSERT INTO import_mapping (source_file_key, sheet_name, source_row_no,"
            " source_record_id, target_type, target_id, mapping_role, merge_note)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [tuple(m[h] for h in MAP_COLS) for m in entities["import_mapping"]])
        con.executemany(
            "INSERT INTO quality_issues (issue_code, severity, source_record_id, detail,"
            " detected_at, status) VALUES (?,?,?,?,?,?)",
            [(i["issue_code"], i["severity"], i.get("source_record_id", ""), i["detail"],
              now_iso(), "open") for i in issues])

        con.executemany("INSERT INTO field_dictionary VALUES (?,?,?,?)",
                        [tuple(f) for f in FIELD_DICTIONARY])

        # 先写发布记录，再统计各表行数，保证 dataset_releases 计数准确
        counts = {}
        for t in TABLES:
            counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        counts["dataset_releases"] = 1
        con.execute("INSERT INTO dataset_releases VALUES (?,?,?,?,?,?,?,?)",
                    (cfg["dataset_version"], cfg["dataset_version"], RULE_VERSION,
                     cfg["id_registry_version"],
                     json.dumps({f["file_key"]: f.get("sha256_before")
                                 for f in manifest["files"]}, ensure_ascii=False),
                     now_iso(), json.dumps(counts, ensure_ascii=False),
                     "由 build_dataset.py 一次构建导出；XLSX 与 SQLite 同源"))
        con.commit()

        fk_errors = con.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        con.close()

    os.replace(tmp, db_path)
    return counts, fk_errors


MODEL_COLS = ["model_id", "brand", "display_name", "display_name_match_key", "release_month",
              "release_month_raw", "release_granularity", "first_source_record_id",
              "cover_asset_id", "note"]
VARIANT_COLS = ["variant_id", "model_id", "variant_name", "name_is_source", "region",
                "region_evidence", "satellite_support", "is_default", "variant_note_raw",
                "source_record_id"]
SKU_COLS = ["sku_id", "variant_id", "model_id", "ram_raw", "ram_gb", "storage_raw",
            "storage_gb", "memory_type", "memory_type_source", "source_record_id"]
PRICE_COLS = ["price_id", "sku_id", "amount", "currency", "price_raw", "price_kind",
              "condition", "is_estimated", "channel", "seller", "price_note", "qualifiers",
              "source_sale_status", "observed_at", "observed_at_kind", "source_id",
              "source_record_id"]
SPEC_COLS = ["spec_id", "subject_type", "subject_id", "variant_id", "spec_key", "unit",
             "value_num", "value_text", "raw_value", "raw_value_type", "source_col",
             "source_record_id", "source_id", "parse_status", "note", "comparison_group",
             "rule_version"]
RUN_COLS = ["run_id", "subject_type", "subject_id", "model_id", "variant_id", "sku_id",
            "benchmark", "institution", "protocol", "protocol_detail", "conditions_json",
            "run_date", "os_version", "comparison_group", "applicable_scope", "scope_note",
            "source_id", "source_record_id", "data_note", "parse_note"]
OBS_COLS = ["observation_id", "run_id", "metric_key", "value_num", "unit", "raw_value",
            "raw_value_type", "missing_reason", "source_col", "source_record_id",
            "comparison_group", "verification_status", "note"]
SOURCE_COLS = ["source_id", "source_kind", "raw_text", "url", "url_count", "publisher",
               "published_at", "published_granularity", "collected_at", "url_is_parseable"]
SNIPPET_COLS = ["snippet_id", "source_id", "subject_type", "subject_id", "related_run_id",
                "related_metric_keys", "snippet_kind", "snippet_text", "source_col",
                "source_ref_col", "source_record_id", "origin", "extraction_method",
                "tag_method", "tags", "tag_count", "note"]
ASSET_COLS = ["asset_id", "model_id", "variant_id", "color_id", "color_name",
              "applicable_scope", "original_zip_path", "extracted_path", "derived_path",
              "asset_key", "format", "width", "height", "size_bytes", "sha256",
              "decode_status", "index_declared_sha256", "sha256_match", "index_width",
              "index_height", "dimension_match", "source_kind", "source_url", "source_page",
              "is_cover", "note"]
COVER_COLS = ["model_id", "cover_asset_id", "cover_rule", "color_name"]
MEMBER_COLS = ["member_path", "is_dir", "is_image", "format", "size_bytes", "compress_size",
               "crc32", "sha256", "path_safe", "extracted", "extract_skip_reason"]
MAP_COLS = ["source_file_key", "sheet_name", "source_row_no", "source_record_id",
            "target_type", "target_id", "mapping_role", "merge_note"]
