"""Safety regressions: traversal or secret copying must never import."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

MODULE = Path(__file__).resolve().parents[1] / 'scripts/import_baseline.py'


class ImportTests(unittest.TestCase):
    def subject(self):
        self.assertTrue(MODULE.exists(), 'safe importer is not implemented')
        spec = importlib.util.spec_from_file_location('import_baseline', MODULE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_windows_zip_paths_rejected(self):
        mod = self.subject()
        for name in ('../x.py', '..\\x.py', 'C:\\x.py', '\\\\host\\share\\x.py',
                     'a/../../x.py', '/x.py', 'a\\..\\x.py', 'a:stream.py'):
            with self.subTest(name=name):
                self.assertFalse(mod.is_safe_member(name))
        self.assertTrue(mod.is_safe_member('providers/中文.py'))

    def test_import_does_not_copy_secrets_or_old_database(self):
        mod = self.subject()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'pipeline'
            src.mkdir()
            (src / 'build_dataset.py').write_text('# reviewed', encoding='utf-8')
            (src / 'catalog.sqlite').write_bytes(b'old')
            zpath = root / 'app.zip'
            with zipfile.ZipFile(zpath, 'w') as z:
                z.writestr('app.py', '# reviewed')
                z.writestr('.env', 'SECRET=do-not-read')
                z.writestr('.streamlit/secrets.toml', 'secret="do-not-read"')
                z.writestr('catalog.sqlite', 'old')
                z.writestr('启动.cmd', 'echo do-not-run')
            out = root / 'output'
            report = mod.import_reviewed_sources(zpath, src, out)
            self.assertEqual((out / 'app.py').read_text(encoding='utf-8'), '# reviewed')
            self.assertFalse((out / '.env').exists())
            self.assertFalse((out / '.streamlit/secrets.toml').exists())
            self.assertFalse((out / 'catalog.sqlite').exists())
            self.assertFalse((out / 'data_pipeline/catalog.sqlite').exists())
            self.assertFalse((out / '启动.cmd').exists())
            self.assertIn('.env', report['skipped'])

    def test_unsafe_archive_rejected_before_writing(self):
        mod = self.subject()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'pipeline'
            src.mkdir()
            zpath = root / 'bad.zip'
            with zipfile.ZipFile(zpath, 'w') as z:
                z.writestr('app.py', '# safe')
                z.writestr('..\\escape.py', '# unsafe')
            out = root / 'output'
            with self.assertRaises(ValueError):
                mod.import_reviewed_sources(zpath, src, out)
            self.assertFalse((out / 'app.py').exists())


if __name__ == '__main__':
    unittest.main()
