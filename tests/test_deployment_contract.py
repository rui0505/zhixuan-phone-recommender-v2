"""Production dependency and no-source-workbook contract in a fresh process."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
class DeploymentTests(unittest.TestCase):
    def test_release_records_actual_clean_dependency_contract(self):
        manifest=json.loads((ROOT/'data/release_manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['runtime_contract']['python_tested'],'3.12.14')
        self.assertEqual(manifest['runtime_contract']['direct_packages']['streamlit'],'1.65.0')
        self.assertEqual(manifest['runtime_contract']['test_environment'],'clean-no-source-archive')
    def test_deployment_runs_without_source_workbooks(self):
        script="""
from streamlit.testing.v1 import AppTest
from pathlib import Path
at=AppTest.from_file(str(Path.cwd()/'app.py'),default_timeout=60).run()
assert not at.exception
at.checkbox(key='manual_confirm').check().run()
next(b for b in at.button if b.label=='确认需求并生成推荐').click().run()
assert not at.exception
assert at.session_state['catalog_result'].status=='ready'
"""
        env=os.environ.copy();env.update(PHONE_ENABLE_LLM='false',LLM_ENV_FILE='.env.must-not-exist-during-tests',PHONE_SOURCE_DIR='/no-source-workbooks')
        run=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
        self.assertEqual(run.returncode,0,run.stderr[-2500:])
    def test_runtime_does_not_import_build_or_web_server_dependencies(self):
        script="""
import sys
from streamlit.testing.v1 import AppTest
from pathlib import Path
framework_modules=set(sys.modules)
at=AppTest.from_file(str(Path.cwd()/'app.py'),default_timeout=60).run()
assert not at.exception
for prefix in ('data_pipeline','docx','web.server'):
    assert not any(n==prefix or n.startswith(prefix+'.') for n in sys.modules),prefix
assert 'web.server' not in sys.modules
# Streamlit 1.65 owns Starlette/Uvicorn/Pillow as framework dependencies.
# Our application must not import the reference server or build/report tools.
"""
        env=os.environ.copy();env.update(PHONE_ENABLE_LLM='false',LLM_ENV_FILE='.env.must-not-exist-during-tests')
        run=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
        self.assertEqual(run.returncode,0,run.stderr[-2500:])
