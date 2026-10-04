import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest


class RebuildEncodingTests(unittest.TestCase):
    def test_rebuild_uses_explicit_utf8_on_windows(self):
        path=Path(__file__).with_name('acceptance.py')
        spec=importlib.util.spec_from_file_location('catalog_acceptance_encoding',path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module,'run_rebuild'))
        with patch.object(module.subprocess,'run') as run:
            module.run_rebuild('config.json')
        self.assertEqual(run.call_args.kwargs.get('encoding'),'utf-8')
        self.assertTrue(run.call_args.kwargs.get('check') is False)
