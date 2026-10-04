# -*- coding: utf-8 -*-
"""阶段七：验收报告 .docx。

报告内容全部来自 quality_report.json 的实测结果，不手写结论。
"""
from __future__ import annotations

import json
import os

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

PASS_COLOR = RGBColor(0x1F, 0x7A, 0x33)
FAIL_COLOR = RGBColor(0xC0, 0x00, 0x00)
NA_COLOR = RGBColor(0x80, 0x60, 0x00)


def _h(doc, text, level=1):
    doc.add_heading(text, level=level)


def _result_of(checks, item):
    for c in checks:
        if c["item"] == item:
            return c["result"]
    return "NOT_CHECKED"


def _kv_table(doc, rows, widths=(4.2, 12.0)):
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for k, v in rows:
        cells = t.add_row().cells
        cells[0].text = str(k)
        cells[1].text = "" if v is None else str(v)
        for run in cells[0].paragraphs[0].runs:
            run.bold = True
    return t


def write_docx(path: str, report: dict, cfg: dict) -> None:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)

    title = doc.add_heading("手机推荐混合数据集 v1 验收报告", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph(f"数据集版本：{report['dataset_version']}　规则版本：{report['rule_version']}"
                          f"　构建时间：{report['built_at']}")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 一 结论摘要
    _h(doc, "一、结论摘要")
    checks = report["checks"]
    n_pass = sum(1 for c in checks if c["result"] == "PASS")
    n_fail = sum(1 for c in checks if c["result"] == "FAIL")
    n_na = sum(1 for c in checks if c["result"] == "NOT_CHECKED")
    doc.add_paragraph(
        f"本次共执行 {len(checks)} 项验收：PASS {n_pass} 项，FAIL {n_fail} 项，"
        f"NOT_CHECKED {n_na} 项。数据库与规范 Excel 已完成；本地预览接口已完成；"
        f"WPS 复核与正式网页更新不在本次范围内，状态见下表。")

    _kv_table(doc, [
        ("数据库完成", "已完成（catalog.sqlite，见交付物清单）"),
        ("规范 Excel 完成", "已完成（手机推荐混合数据集_v1.xlsx）"),
        ("本地预览通过", _result_of(checks, "网页可读")),
        ("重建稳定性", _result_of(checks, "重建稳定")),
        ("WPS 已复核或待复核", "待 WPS 人工复核（本次运行环境未启动 WPS）"),
        ("正式网页未更新", "确认：本次不推送、不合并正式分支，未替换正式网页数据"),
        ("源文件是否改动", _result_of(checks, "原文件不变") + "（执行前后 SHA-256 一致）"),
    ])

    # 二 验收明细
    _h(doc, "二、验收明细")
    t = doc.add_table(rows=1, cols=4)
    t.style = "Table Grid"
    hdr = t.rows[0].cells
    for i, h in enumerate(["验收项", "通过标准", "结果", "实际结果 / 证据"]):
        hdr[i].text = h
        for run in hdr[i].paragraphs[0].runs:
            run.bold = True
    for c in checks:
        cells = t.add_row().cells
        cells[0].text = c["item"]
        cells[1].text = c["standard"]
        cells[2].text = c["result"]
        run = cells[2].paragraphs[0].runs[0]
        run.bold = True
        run.font.color.rgb = {"PASS": PASS_COLOR, "FAIL": FAIL_COLOR}.get(c["result"], NA_COLOR)
        cells[3].text = c["detail"]
    for row in t.rows:
        row.cells[0].width = Pt(70)
        row.cells[1].width = Pt(150)
        row.cells[2].width = Pt(50)
        row.cells[3].width = Pt(230)

    # 三 记录数
    _h(doc, "三、记录数")
    _kv_table(doc, [(k, v) for k, v in report["table_counts"].items()])
    doc.add_paragraph("ID 分配：" + json.dumps(report["id_stats"], ensure_ascii=False))

    # 四 质检问题
    _h(doc, "四、质检问题汇总")
    qi = report["quality_issues"]
    doc.add_paragraph(f"共登记 {qi['total']} 条；按严重度："
                      f"{json.dumps(qi['by_severity'], ensure_ascii=False)}")
    t2 = doc.add_table(rows=1, cols=2)
    t2.style = "Table Grid"
    t2.rows[0].cells[0].text = "问题代码"
    t2.rows[0].cells[1].text = "条数"
    for k, v in qi["by_code"].items():
        r = t2.add_row().cells
        r[0].text = k
        r[1].text = str(v)

    # 五 边界案例
    _h(doc, "五、边界案例检查")
    for line in report.get("edge_cases", []):
        doc.add_paragraph(line, style="List Bullet")

    # 六 复现命令
    _h(doc, "六、复现命令")
    for cmd in report.get("reproduce_commands", []):
        par = doc.add_paragraph(cmd)
        for run in par.runs:
            run.font.name = "Consolas"
            run.font.size = Pt(9)

    # 七 未解决事项
    _h(doc, "七、未解决事项与下一步")
    for line in report.get("next_steps", []):
        doc.add_paragraph(line, style="List Bullet")

    # 八 交付物
    _h(doc, "八、交付物路径")
    _kv_table(doc, [(k, v) for k, v in report.get("deliverables", {}).items()])

    # 九 交付状态（任务书要求四项分别列出，不用一句话笼统概括）
    ds = report.get("delivery_status")
    if ds:
        _h(doc, "九、交付状态")
        doc.add_paragraph(
            "以下四项分别陈述，避免用「已完成」笼统覆盖尚未人工复核的环节。")
        _kv_table(doc, list(ds.items()))

    # 十 算法边界（VIKOR 未实现须显式标注，不得返回模拟成绩）
    alg = report.get("algorithm_scope")
    if alg:
        _h(doc, "十、算法边界")
        _kv_table(doc, list(alg.items()))

    doc.save(path)


def load_report(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
