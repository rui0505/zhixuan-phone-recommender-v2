# -*- coding: utf-8 -*-
"""公共工具：路径、哈希、文本规范化、数值保真序列化、CSV/JSON 读写。

本模块只做与业务无关的机械操作，所有业务规则集中在 rules.py。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------- 编码

def force_utf8_stdio() -> None:
    """终端统一 UTF-8，避免中文在 Windows 控制台被错误编码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------- 路径

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["config_path"] = os.path.abspath(path)
    for key, env_name in (("source_dir", "PHONE_SOURCE_DIR"), ("output_dir", "PHONE_BUILD_DIR")):
        if os.environ.get(env_name):
            cfg[key] = os.environ[env_name]
    cfg.setdefault('workbook_name', '手机推荐混合数据集_' + cfg.get('dataset_version', '未命名') + '.xlsx')
    cfg.setdefault('report_name', '验收报告_' + cfg.get('dataset_version', '未命名') + '.docx')
    return cfg


def resolve_dirs(cfg: dict) -> dict:
    src = os.path.abspath(cfg["source_dir"])
    out = os.path.abspath(cfg["output_dir"])
    if src == out or os.path.commonpath([src, out]) == src:
        raise ValueError('Output must not be inside the original source directory')
    return {
        "source_dir": src,
        "output_dir": out,
        "raw_snapshot": os.path.join(out, "raw_snapshot"),
        "assets_extracted": os.path.join(out, "assets_extracted"),
        "assets_derived": os.path.join(out, "assets_derived"),
        "logs": os.path.join(out, "logs"),
    }


def ensure_dirs(paths: dict) -> None:
    for key in ("output_dir", "raw_snapshot", "assets_extracted", "assets_derived", "logs"):
        os.makedirs(paths[key], exist_ok=True)


# ---------------------------------------------------------------- 哈希

def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def business_digest(db_path) -> str:
    """Stable business content, including values and links; omit local build metadata."""
    import sqlite3
    tables = ('phone_models','phone_variants','skus','price_snapshots','specifications','spec_provenance',
              'benchmark_runs','metric_observations','metric_definitions','sources','assets',
              'asset_colors','model_covers','evidence_snippets','import_mapping','dataset_releases','field_dictionary')
    excluded = {'built_at','release_id','extracted_path','mapping_id'}
    con = sqlite3.connect(str(db_path))
    result = {}
    try:
        available = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table not in available:
                continue
            columns = [r[1] for r in con.execute(f'PRAGMA table_info({table})') if r[1] not in excluded]
            rows = [list(r) for r in con.execute(f'SELECT {",".join(columns)} FROM {table}')]
            rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
            result[table] = {'columns':columns,'rows':rows}
    finally:
        con.close()
    return sha256_bytes(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',',':'),allow_nan=False).encode('utf-8'))


def file_stat(path: str) -> dict:
    st = os.stat(path)
    return {
        "size_bytes": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
    }


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 数值保真

def value_type_of(v) -> str:
    if v is None:
        return "empty"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    return type(v).__name__


def value_to_text(v) -> str:
    """把单元格值转成可无损恢复的文本。

    - 数值用 repr 输出，避免 float 的 JSON/CSV 往返精度损失（repr 是 Python 的最短往返表示）。
    - None 用空串，但另用 value_type='empty' 区分真正的空单元格。
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    return str(v)


def text_to_value(t: str, vtype: str):
    """按 value_type 还原原始值，用于重建校验。"""
    if vtype == "empty":
        return None
    if vtype == "bool":
        return t == "true"
    if vtype == "int":
        return int(t)
    if vtype == "float":
        return float(t)
    return t


def normalize_match_key(s) -> str:
    """仅用于匹配键：统一空白、大小写、全半角。原始名称不变。"""
    if s is None:
        return ""
    s = str(s)
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out).strip().lower()


def has_suspicious_encoding(s) -> bool:
    """检测替换字符或典型乱码串。"""
    if not isinstance(s, str):
        return False
    if "\ufffd" in s:
        return True
    return any(tok in s for tok in ("鏁", "锟斤拷", "Ã¦", "â€"))


# ---------------------------------------------------------------- IO

def write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_csv_bom(path: str, header: list, rows: list) -> None:
    """UTF-8 BOM CSV，供 WPS 直接打开；正确引用含逗号/换行/引号的字段。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def write_jsonl(path: str, rows) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False))
            fh.write("\n")
