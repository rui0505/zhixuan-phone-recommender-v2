"""Readable acceptance report; every status comes from the actual JSON record."""
import json
import os
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


def _table(doc, headings, rows, widths):
    table=doc.add_table(rows=1,cols=len(headings)); table.autofit=False
    table.alignment=WD_TABLE_ALIGNMENT.CENTER
    for cell,text in zip(table.rows[0].cells,headings): cell.text=str(text)
    for row in rows:
        for cell,text in zip(table.add_row().cells,row): cell.text='' if text is None else str(text)
    borders=OxmlElement('w:tblBorders')
    for edge in ('top','bottom','left','right','insideH','insideV'):
        node=OxmlElement('w:'+edge);node.set(qn('w:val'),'single');node.set(qn('w:sz'),'4');node.set(qn('w:color'),'D9D9D9');borders.append(node)
    table._tbl.tblPr.append(borders)
    repeat=OxmlElement('w:tblHeader');table.rows[0]._tr.get_or_add_trPr().append(repeat)
    for i,row in enumerate(table.rows):
        row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
        for j,cell in enumerate(row.cells):
            cell.width=Inches(widths[j]);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'E8EEF3' if i==0 else 'FFFFFF');cell._tc.get_or_add_tcPr().append(shade)
            for p in cell.paragraphs:
                p.paragraph_format.space_before=Pt(4);p.paragraph_format.space_after=Pt(4)
                p.paragraph_format.line_spacing=1.15
                for run in p.runs:
                    run.font.size=Pt(10.5);run.bold=i==0
    doc.add_paragraph().paragraph_format.space_after=Pt(0)


def write_docx(path, report, cfg):
    doc=Document();section=doc.sections[0]
    section.page_width=Inches(8.5);section.page_height=Inches(11)
    section.top_margin=Inches(.7);section.bottom_margin=Inches(.7)
    section.left_margin=Inches(.7);section.right_margin=Inches(.7)
    for name in ('Normal','Title','Heading 1','Heading 2'):
        style=doc.styles[name];style.font.name='Microsoft YaHei';style.font.color.rgb=RGBColor(0,0,0)
        style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    doc.styles['Normal'].font.size=Pt(11)
    doc.styles['Normal'].paragraph_format.line_spacing=1.2
    doc.styles['Normal'].paragraph_format.space_after=Pt(6)
    doc.add_paragraph('手机混合数据库验收报告',style='Title')
    doc.add_paragraph(f"版本 {report['dataset_version']} 规则 {report['rule_version']}\n构建时间 {report['built_at']}")
    doc.add_paragraph(f"机器验收状态为 {report.get('machine_acceptance','PENDING')}，总体验收状态为 {report.get('overall_acceptance','PENDING')}。"
                      '本报告供团队核对导入结果和确定是否适合发布。机器检查、人工抽查、WPS 复核与网页部署分别记录，不能相互替代。')
    doc.add_heading('交付状态',level=1)
    _table(doc,['事项','实际状态'],list(report.get('delivery_status',{}).items()),[1.8,5.3])
    doc.add_heading('机器检查',level=1)
    _table(doc,['检查项','结果','证据摘要'],[(c['item'],c['result'],c['detail']) for c in report.get('machine_checks',[])],[1.3,.9,4.9])
    doc.add_heading('人工复核',level=1)
    _table(doc,['检查项','状态','完成情况'],[(c['item'],c['result'],str(c.get('completed','未记录'))+' / '+str(c.get('target','需实际复核'))) for c in report.get('manual_checks',[])],[1.8,1.3,4.0])
    doc.add_paragraph('跨品牌抽查清单仅为待办记录；没有填写复核人和证据的条目不算通过。WPS 未实际检查时保留 NOT_CHECKED。')
    doc.add_heading('数据库记录数',level=1)
    _table(doc,['业务表','记录数'],list(report.get('table_counts',{}).items()),[5.3,1.8])
    doc.add_paragraph('ID 分配 '+json.dumps(report.get('id_stats',{}),ensure_ascii=False))
    doc.add_heading('质检问题和数据边界',level=1)
    qi=report.get('quality_issues',{})
    doc.add_paragraph(f"登记问题 {qi.get('total',0)} 条，严重度分布 {json.dumps(qi.get('by_severity',{}),ensure_ascii=False)}。问题登记不代表已人工解决。")
    _table(doc,['问题代码','条数'],list(qi.get('by_code',{}).items()),[5.3,1.8])
    for line in report.get('edge_cases',[]): doc.add_paragraph(line)
    doc.add_heading('算法范围',level=1)
    for key,value in report.get('algorithm_scope',{}).items(): doc.add_paragraph(f'{key} {value}')
    doc.add_heading('复现和待办',level=1)
    for line in report.get('reproduce_commands',[]):
        if line: doc.add_paragraph(line)
    for line in report.get('next_steps',[]): doc.add_paragraph(line)
    doc.add_heading('本地交付物',level=1)
    doc.add_paragraph('文件位于本次构建输出目录。原件、完整评测摘录和未审核图片不作为线上发布内容。')
    _table(doc,['文件用途','文件名'],[(key,os.path.basename(value.rstrip('/\\'))) for key,value in report.get('deliverables',{}).items()],[1.8,5.3])
    doc.save(path)


def load_report(path):
    with open(path,encoding='utf-8') as handle: return json.load(handle)
