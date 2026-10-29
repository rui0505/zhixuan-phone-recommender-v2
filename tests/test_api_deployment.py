import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from starlette.testclient import TestClient
from api.app import create_app
from api.contracts import parse_recommendation
from tests.api_fixture import request_payload
from catalog.adapter import build_input
from catalog.evidence import build_evidence
from catalog.release import ReleaseStore
from ranking_modes import rank

ROOT=Path(__file__).resolve().parents[1]

class DeploymentTests(unittest.TestCase):
    def test_api_only_import(self):
        code="import api.app,sys; forbidden={'streamlit','web.server','data_pipeline','docx'}; assert not forbidden.intersection(sys.modules)"
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_two_entrypoints_share_result(self):
        app=create_app(); service=app.state.service; release=service.store.get(); payload=request_payload(release)
        request=parse_recommendation(payload,service.meta()['brands']); outcome=service.recommend(request)
        di=build_input(release.repo,request.needs,request.weights,request.target_screen,request.mode)
        result=rank(di,target_screen=request.target_screen,method='hybrid_rrf')
        evidence=build_evidence(di,result,request.needs)
        self.assertEqual(result.ranking_sha256,outcome.result.ranking_sha256)
        self.assertEqual(result.decision_sha256,outcome.result.decision_sha256)
        self.assertEqual(result.ranked['score'].tolist(),outcome.result.ranked['score'].tolist())
        self.assertEqual(evidence['decision_sha256'],outcome.bundle['decision_sha256'])
    def test_runtime_pins_match(self):
        pins=lambda name:dict(line.split('==') for line in (ROOT/name).read_text().splitlines() if '==' in line)
        api=pins('requirements-api.txt'); old=pins('requirements.txt')
        for package in ('pandas','numpy','openpyxl'):self.assertEqual(api[package],old[package])
        self.assertNotIn('streamlit',api)
    def test_delivery_real_http_smoke_exists_and_runs(self):
        result=subprocess.run([sys.executable,'scripts/api_smoke.py','--self-host'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(result.stdout); # 2026-10-26：默认权重新增 电池2/处理器档位2，缺这两项数据的机型失去资格，
        # 候选从 51 条降到 40 条。拍照默认保持 0（DXOMARK 覆盖仅 11/51）。
        self.assertEqual(report['ranked_total'],40);self.assertEqual(report['formats'],['csv','xlsx','json'])
    def test_release_unchanged(self):
        for name,digest in [('catalog.sqlite','128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8'),('release_manifest.json','d7d899e07c540deab0ff01ae19555765cd159809dd7c7bce4b3499b87b45ee22')]:
            self.assertEqual(hashlib.sha256((ROOT/'data'/name).read_bytes()).hexdigest(),digest)
