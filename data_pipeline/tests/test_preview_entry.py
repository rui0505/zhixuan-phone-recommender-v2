import subprocess
import sys
from pathlib import Path
import unittest


class PreviewEntryTests(unittest.TestCase):
    def test_preview_cli_imports_shared_catalog_package(self):
        root=Path(__file__).resolve().parents[2]
        result=subprocess.run([sys.executable,str(root/'data_pipeline/data_preview.py'),'--help'],cwd=root,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--model',result.stdout)
