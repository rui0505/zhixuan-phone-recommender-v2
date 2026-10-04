"""Independent tiny, synthetic fixtures; never edit real source workbooks."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'data_pipeline/src'))
sys.path.insert(0, str(ROOT))
import openpyxl
import stage_manifest
from rules import parse_price


class SourceContractTests(unittest.TestCase):
    def fixture(self, root):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = '输入'
        ws.append(['产品名称', '值', '公式'])
        ws.append(['合成测试机', 0, '=B2+1'])
        ws.append(['空值测试', None, None])
        ws['B2'].number_format = '0.00'
        path = root / 'source.xlsx'
        wb.save(path)
        wb.close()
        return {'source_dir': str(root), 'inputs': {'price_main': 'source.xlsx'},
                'primary_sheet': '输入', 'required_fields': {'price_main': ['产品名称', '值']}}

    def audit(self):
        fn = getattr(stage_manifest, 'audit_source_cells', None)
        self.assertTrue(callable(fn), 'set-based source audit not implemented')
        return fn

    def test_cell_set_detects_removed_blank_and_formula_cells(self):
        fn = self.audit()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.fixture(Path(tmp))
            cells = stage_manifest.read_workbook(str(Path(tmp) / 'source.xlsx'), 'price_main')[2]
            for coordinate in ((3, 2), (2, 3)):
                trimmed = [c for c in cells if (c['source_row_no'], c['source_col_no']) != coordinate]
                result = fn(cfg, trimmed)
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual(len(result['missing']), 1)

    def test_source_types_and_formats_round_trip(self):
        fn = self.audit()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.fixture(Path(tmp))
            cells = stage_manifest.read_workbook(str(Path(tmp) / 'source.xlsx'), 'price_main')[2]
            self.assertEqual(fn(cfg, cells)['status'], 'PASS')
            zero = next(c for c in cells if c['source_row_no'] == 2 and c['source_col_no'] == 2)
            self.assertEqual((zero['raw_value_text'], zero['raw_value_type'], zero['number_format']), ('0', 'int', '0.00'))
            zero['number_format'] = 'General'
            self.assertEqual(len(fn(cfg, cells)['changed']), 1)

    def test_missing_required_sheet_fails_preflight(self):
        import importlib.util
        path = ROOT / 'scripts/validate_inputs.py'
        self.assertTrue(path.exists(), 'input preflight not implemented')
        spec = importlib.util.spec_from_file_location('validate_inputs', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            import json
            root = Path(tmp)
            cfg = self.fixture(root)
            cfg['primary_sheet'] = '不存在的表'
            config_path = root / 'cfg.json'
            config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(mod.validate_inputs(config_path)['status'], 'FAIL')

    def test_range_is_not_first_digit_price(self):
        result = parse_price('约4000–5000元')
        self.assertIsNone(result['amount'])
        self.assertEqual(result['status'], 'ambiguous_multi_number')


if __name__ == '__main__':
    unittest.main()
