# -*- coding: utf-8 -*-
"""阶段四：图片资源清单、解压与网页副本。

安全约束：
- ZIP 成员路径含 '..'、绝对路径或盘符的一律拒绝解压，并登记问题。
- 原图不覆盖、不裁剪、不改色；网页副本另存 assets_derived/ 并保留原图。
- 解码失败不伪造替代图，只登记状态。
"""
from __future__ import annotations

import csv
import io
import json
import os
import posixpath
import zipfile

from common import normalize_match_key, sha256_bytes, value_to_text

try:
    import pillow_avif  # noqa: F401  注册 AVIF 解码器
    AVIF_PLUGIN = True
except Exception:
    AVIF_PLUGIN = False

from PIL import Image

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".bmp", ".tif", ".tiff"}

ASSET_HEADER = ["asset_id", "model_id", "variant_id", "color_id", "color_name",
                "applicable_scope", "original_zip_path", "extracted_path", "derived_path",
                "asset_key", "format", "width", "height", "size_bytes", "sha256",
                "decode_status", "index_declared_sha256", "sha256_match", "index_width",
                "index_height", "dimension_match", "source_kind", "source_url", "source_page",
                "is_cover", "note"]
MEMBER_HEADER = ["member_path", "is_dir", "is_image", "format", "size_bytes",
                 "compress_size", "crc32", "sha256", "path_safe", "extracted",
                 "extract_skip_reason"]


def is_safe_member(name: str) -> bool:
    if not name or '\x00' in name or ':' in name or '..' in name.replace('\\','/').split('/'):
        return False
    if name.startswith("/") or name.startswith("\\"):
        return False
    if len(name) > 1 and name[1] == ":":
        return False
    norm = posixpath.normpath(name.replace('\\','/'))
    return not (norm.startswith("../") or norm == ".." or "/../" in norm)


def build_assets(cfg, paths, model_id_by_key, model_order, issues):
    zip_path = os.path.join(paths["source_dir"], cfg["inputs"]["images_zip"])
    if not os.path.exists(zip_path):
        issues.append(dict(issue_code="ZIP_MISSING", severity="high",
                           source_record_id="", detail="图片包不存在，跳过资源阶段"))
        return {"assets": [], "members": [], "covers": [], "stats": {}}

    max_w = cfg["conventions"]["webp_max_width"]
    quality = cfg["conventions"]["webp_quality"]

    members, assets, covers = [], [], []
    color_ids = {}
    cover_by_model = {}
    stats = {"members": 0, "images": 0, "decoded": 0, "decode_failed": 0,
             "derived": 0, "models_covered": 0, "colors": 0, "metadata_files": 0,
             "crc_error": None, "unsafe_paths": 0, "avif_plugin": AVIF_PLUGIN}

    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()
        stats["crc_error"] = bad
        if bad:
            issues.append(dict(issue_code="ZIP_CRC_ERROR", severity="high",
                               source_record_id="", detail=f"ZIP CRC 校验失败成员: {bad}"))

        index_map = {}
        index_raw = None
        for name in z.namelist():
            if name.endswith("图片索引.json"):
                index_raw = json.loads(z.read(name).decode("utf-8-sig"))
        if index_raw:
            for item in index_raw:
                index_map[item.get("file", "")] = item

        color_order = {}
        for name in z.namelist():
            if name.endswith("型号与颜色目录.csv"):
                txt = z.read(name).decode("utf-8-sig")
                for row in csv.DictReader(io.StringIO(txt)):
                    color_order[row["手机型号"]] = [c.strip() for c in
                                                    (row.get("已收集颜色") or "").split("、") if c.strip()]

        for info in z.infolist():
            name = info.filename
            stats["members"] += 1
            safe = is_safe_member(name) and ((info.external_attr >> 16) & 0o170000) != 0o120000
            ext = os.path.splitext(name)[1].lower()
            is_img = (not info.is_dir()) and ext in IMAGE_EXT
            rec = {
                "member_path": name, "is_dir": 1 if info.is_dir() else 0,
                "is_image": 1 if is_img else 0, "format": ext.lstrip(".") if ext else "",
                "size_bytes": info.file_size, "compress_size": info.compress_size,
                "crc32": f"{info.CRC:08X}", "sha256": "", "path_safe": 1 if safe else 0,
                "extracted": 0, "extract_skip_reason": "",
            }
            if not safe:
                stats["unsafe_paths"] += 1
                rec["extract_skip_reason"] = "路径含 .. 或绝对路径，拒绝解压"
                issues.append(dict(issue_code="ZIP_UNSAFE_PATH", severity="high",
                                   source_record_id=name,
                                   detail="ZIP 成员路径不安全，已拒绝解压"))
                members.append(rec)
                continue
            if info.is_dir():
                rec["extract_skip_reason"] = "目录条目"
                members.append(rec)
                continue

            data = z.read(name)
            rec["sha256"] = sha256_bytes(data)
            target = os.path.join(paths["assets_extracted"], *name.replace('\\','/').split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if not os.path.exists(target):
                with open(target, "wb") as fh:
                    fh.write(data)
            rec["extracted"] = 1
            members.append(rec)

            if not is_img:
                stats["metadata_files"] += 1
                continue

            stats["images"] += 1
            idx = index_map.get(name, {})
            model_name = idx.get("model") or _model_from_path(name)
            color_name = idx.get("color") or _color_from_path(name)
            mkey = normalize_match_key(model_name)
            mid = model_id_by_key.get(mkey)
            if mid is None:
                issues.append(dict(issue_code="ASSET_MODEL_UNMATCHED", severity="high",
                                   source_record_id=name,
                                   detail=f"图片所属机型 {model_name!r} 未匹配到 model_id，"
                                          f"asset 仍保留但 model_id 为 NULL"))
            ckey = (mid, color_name)
            if ckey not in color_ids:
                color_ids[ckey] = f"C{len(color_ids) + 1:04d}"
            color_id = color_ids[ckey]

            decode_status, w, h = decode_check(data)
            if decode_status == "ok":
                stats["decoded"] += 1
            else:
                stats["decode_failed"] += 1
                issues.append(dict(issue_code="IMAGE_DECODE_FAILED", severity="medium",
                                   source_record_id=name,
                                   detail=f"{ext} 图片解码失败（{decode_status}），"
                                          f"未生成网页副本，未伪造替代图"))

            asset_id = f"A{stats['images']:04d}"
            derived_rel = ""
            if decode_status == "ok":
                key = f"{mid or 'M_UNKNOWN'}/{color_id}/{asset_id}.webp"
                out_path = os.path.join(paths["assets_derived"], *key.split("/"))
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                ok, note = make_webp(data, out_path, max_w, quality)
                if ok:
                    derived_rel = key
                    stats["derived"] += 1
                else:
                    issues.append(dict(issue_code="WEBP_DERIVE_FAILED", severity="medium",
                                       source_record_id=name, detail=f"网页副本生成失败：{note}"))
            else:
                key = f"{mid or 'M_UNKNOWN'}/{color_id}/{asset_id}.{ext.lstrip('.')}"

            idx_sha = idx.get("sha256", "")
            dim_ok = ""
            if idx.get("width") and idx.get("height") and w and h:
                dim_ok = 1 if (idx["width"] == w and idx["height"] == h) else 0
                if dim_ok == 0:
                    issues.append(dict(issue_code="IMAGE_DIMENSION_MISMATCH", severity="medium",
                                       source_record_id=name,
                                       detail=f"索引声明 {idx['width']}x{idx['height']}，"
                                              f"实际解码 {w}x{h}"))
            if idx_sha and idx_sha != rec["sha256"]:
                issues.append(dict(issue_code="IMAGE_SHA256_MISMATCH", severity="medium",
                                   source_record_id=name,
                                   detail="图片索引声明的 SHA256 与实际成员内容不一致"))

            assets.append({
                "asset_id": asset_id, "model_id": mid, "variant_id": None,
                "color_id": color_id, "color_name": color_name,
                "applicable_scope": "model",
                "original_zip_path": name,
                "extracted_path": os.path.relpath(target, paths["output_dir"]).replace("\\", "/"),
                "derived_path": derived_rel, "asset_key": key,
                "format": ext.lstrip("."), "width": w, "height": h,
                "size_bytes": info.file_size, "sha256": rec["sha256"],
                "decode_status": decode_status,
                "index_declared_sha256": idx_sha,
                "sha256_match": (1 if idx_sha == rec["sha256"] else (0 if idx_sha else "")),
                "index_width": idx.get("width", ""), "index_height": idx.get("height", ""),
                "dimension_match": dim_ok,
                "source_kind": idx.get("kind", ""), "source_url": idx.get("url", ""),
                "source_page": idx.get("page", ""), "is_cover": 0,
                "note": idx.get("note", ""),
            })

    # ---- 封面映射：按「型号与颜色目录.csv」的颜色顺序取第一张 ----
    order = {}
    for a in assets:
        order.setdefault(a["model_id"], []).append(a)
    for m in model_order:
        mid = m["model_id"]
        lst = order.get(mid, [])
        if not lst:
            issues.append(dict(issue_code="MODEL_HAS_NO_IMAGE", severity="info",
                               source_record_id=mid,
                               detail=f"{m['display_name']} 无图片，网页显示文字占位"))
            continue
        preferred = color_order.get(m["display_name"], [])
        chosen = None
        for c in preferred:
            for a in lst:
                if a["color_name"] == c:
                    chosen = a
                    break
            if chosen:
                break
        if chosen is None:
            chosen = lst[0]
        chosen["is_cover"] = 1
        covers.append({"model_id": mid, "cover_asset_id": chosen["asset_id"],
                       "cover_rule": "按 型号与颜色目录.csv 颜色顺序取第一张；无该文件时取 asset_id 最小者",
                       "color_name": chosen["color_name"]})

    stats["models_covered"] = len(set(a["model_id"] for a in assets if a["model_id"]))
    stats["colors"] = len(color_ids)

    # 缺失配色清单 -> 问题登记
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.endswith("待补充清单.csv"):
                txt = z.read(name).decode("utf-8-sig")
                for row in csv.DictReader(io.StringIO(txt)):
                    issues.append(dict(issue_code="COLOR_PENDING_SUPPLEMENT", severity="info",
                                       source_record_id=row.get("手机型号", ""),
                                       detail=f"待补充配色：{row.get('待补充说明','')}"))

    return {"assets": assets, "members": members, "covers": covers, "stats": stats}


def _model_from_path(name):
    parts = name.split("/")
    return parts[1] if len(parts) > 1 else ""


def _color_from_path(name):
    parts = name.split("/")
    return parts[2] if len(parts) > 2 else ""


def decode_check(data: bytes):
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            return "ok", im.width, im.height
    except Exception as exc:  # noqa: BLE001
        return f"failed: {type(exc).__name__}", None, None


def make_webp(data: bytes, out_path: str, max_w: int, quality: int):
    """生成网页副本：等比缩放，不裁剪、不改色。"""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            w, h = im.size
            if w > max_w:
                nh = max(1, round(h * max_w / w))
                im = im.resize((max_w, nh), Image.LANCZOS)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
            else:
                im = im.convert("RGB")
            im.save(out_path, "WEBP", quality=quality, method=4)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
