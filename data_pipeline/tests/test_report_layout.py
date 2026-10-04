import sys
from pathlib import Path
import tempfile
import unittest
from docx import Document
from docx.oxml.ns import qn
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from stage_report import write_docx


class ReportLayoutTests(unittest.TestCase):
    def test_table_rows_do_not_split_across_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'report.docx'
            write_docx(path,{'dataset_version':'test','rule_version':'test','built_at':'test',
                             'delivery_status':{'数据库':'PENDING'}},{})
            doc=Document(path)
            for table in doc.tables:
                for row in table.rows:
                    self.assertIsNotNone(row._tr.find('./'+qn('w:trPr')+'/'+qn('w:cantSplit')))
