"""One-release operator script: verify in isolation, then explicitly activate.

No package installs, credential output, network configuration, or data rebuild.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

BASE=Path('/opt/zhixuan')
PYTHON=BASE/'.venv/bin/python'
SNAPSHOT='128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8'
BACKUP=Path('/root/zhixuan-buyer-reviews-20261027-backup')
EXPECTED_PREVIOUS=BASE/'releases/alpine-ui-20261027-ccd0413'
EXPECTED_ARCHIVE='cf11b7e8f1462b5494fd9c67252d3e90a8010ce17314d36e32a9d1212992ad8e'
EXPECTED_NAME='alpine-ui-20261027-buyerreviews'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args,**kwargs):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=60,**kwargs).stdout.strip()


def get(base,path,body=None):
    request=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)


def wait_ready(base):
    end=time.monotonic()+25
    while time.monotonic()<end:
        try:return get(base,'/readyz')
        except Exception:time.sleep(.3)
    raise RuntimeError('API readiness timeout')


def runtime_check(base):
    meta=get(base,'/api/v1/meta')
    assert meta['release']['snapshot_sha256']==SNAPSHOT
    assert meta['defaults']['evaluation_mode']=='spec_reference'
    assert sum(x['count'] for x in meta['source_summary'])==meta['counts']['sources']
    body={**meta['defaults'],'needs':{'budget':{'min':None,'max':None},'min_storage':None,
        'brands':{'include':[],'exclude':[]}},'method':'hybrid_rrf','game_scope':'unsupported',
        'confirmed':True,'expected_release':meta['release'],
        'expected_reference_rule_version':meta['reference_rule_version']}
    result=get(base,'/api/v1/recommend?page_size=3',body)
    assert result['status']=='ready' and result['ranked_total']==40
    assert all(len(card['reference_scores'])==7 for card in result['ranked'])
    assert '/100' not in str([r['summary'] for r in result['recommendation_reasons'].values()])
    reviews=get(base,'/api/v1/skus/S0462/reviews?page_size=20')
    assert reviews['model']['model_id']=='M0140' and reviews['sample_total']==132
    assert reviews['release']==meta['release'] and all('username' in item for item in reviews['items'])
    bad=get(base,'/api/v1/skus/S0462/reviews?grade=bad&page_size=20')
    assert bad['total']==19 and all(item['grade']=='bad' for item in bad['items'])
    with urllib.request.urlopen(base+'/',timeout=20) as response: html=response.read().decode()
    assert 'id="view-buyer-reviews"' in html and '查看淘宝买家评价' in html
    with urllib.request.urlopen(base+'/reviews.mjs',timeout=20) as response: assert response.status==200
    with urllib.request.urlopen(base+'/reviews.css',timeout=20) as response: assert response.status==200
    return {'ready':True,'ranked_total':result['ranked_total'],'rule':meta['reference_rule_version'],
        'source_count':meta['counts']['sources'],'decision_sha256':result['decision_sha256'],
        'ranking_sha256':result['ranking_sha256']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--release-name',required=True)
    parser.add_argument('--phase',choices=('verify','activate'),required=True)
    args=parser.parse_args()
    assert os.geteuid()==0 and PYTHON.is_file()
    assert args.release_name==EXPECTED_NAME and args.sha256==EXPECTED_ARCHIVE
    archive=Path(args.archive).resolve()
    assert archive.parent==Path('/tmp') and sha(archive)==args.sha256
    release=BASE/'releases'/args.release_name
    assert release.resolve().parent==(BASE/'releases').resolve()
    old=(BASE/'current').resolve()
    assert old.parent==(BASE/'releases').resolve()
    assert old==EXPECTED_PREVIOUS.resolve(), 'Production changed since last known release; stop and inspect before proceeding'
    assert sha(old/'data/catalog.sqlite')==SNAPSHOT
    configs=[Path('/etc/systemd/system/zhixuan.service'),Path('/etc/zhixuan.env')]
    config_hashes={str(p):sha(p) for p in configs if p.exists()}
    quota=None
    env_path=Path('/etc/zhixuan.env')
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('PHONE_NEEDS_QUOTA_PATH='):
                quota=Path(line.split('=',1)[1].strip().strip('\"\''))
                assert quota.is_absolute()
                assert not quota.resolve().is_relative_to(BASE/'releases'), 'Quota must persist outside release trees'
                assert not quota.is_relative_to(BASE/'current'), 'Quota must not traverse current'
    BACKUP.mkdir(exist_ok=True)
    marker=BACKUP/(args.release_name+'.json')
    if args.phase=='verify':
        assert not release.exists(), 'Use a fresh release name; no existing release is overwritten'
        release.mkdir()
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                path=Path(info.filename)
                assert not path.is_absolute() and '..' not in path.parts
                assert not stat.S_ISLNK(info.external_attr>>16)
            z.extractall(release)
        manifest=json.loads((release/'bundle_manifest.json').read_text())
        for name,digest in manifest['files'].items():assert sha(release/name)==digest,name
        assert sha(release/'data/catalog.sqlite')==SNAPSHOT
        for path in release.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        release.chmod(0o755)
        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PHONE_CATALOG_PATH','LLM_API_KEY')}
        env['PHONE_ENABLE_LLM']='false'
        data_manifest=json.loads((release/'data/release_manifest.json').read_text())
        assert data_manifest['status']=='PASS' and data_manifest['acceptance']['machine_acceptance']=='PASS'
        with sqlite3.connect((release/'data/catalog.sqlite').as_uri()+'?mode=ro',uri=True) as con:
            assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert not con.execute('PRAGMA foreign_key_check').fetchall()
        log=BACKUP/(args.release_name+'-staging.log')
        # Fail before launch if this dedicated loopback staging port is occupied.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',18671))
        with log.open('w') as stream:
            process=subprocess.Popen([str(PYTHON),'-m','uvicorn','api.app:app','--host','127.0.0.1',
                '--port','18671','--log-level','error'],cwd=release,env=env,stdout=stream,stderr=stream)
            try:
                wait_ready('http://127.0.0.1:18671')
                assert process.poll() is None, 'Isolated staging process did not start'
                check=runtime_check('http://127.0.0.1:18671')
                smoke=json.loads(run([str(PYTHON),'scripts/api_smoke.py','--base','http://127.0.0.1:18671'],cwd=release,env=env))
            finally:
                process.terminate();process.wait(timeout=10)
        assert all(sha(Path(path))==digest for path,digest in config_hashes.items())
        report={'phase':'verified','release':str(release),'previous':str(old),'archive_sha256':args.sha256,
            'files':len(manifest['files']),'configs':config_hashes,'runtime':check,'smoke':smoke,
            'quota_outside_release':True,'quota_present':bool(quota and quota.exists())}
        marker.write_text(json.dumps(report,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='configs'}))
        return
    report=json.loads(marker.read_text())
    assert report['phase']=='verified' and report['archive_sha256']==args.sha256
    assert str(old)==report['previous'] and str(release)==report['release']
    manifest=json.loads((release/'bundle_manifest.json').read_text())
    for name,digest in manifest['files'].items():assert sha(release/name)==digest,name
    assert all(sha(Path(path))==digest for path,digest in report['configs'].items())
    temporary=BASE/('current.next.'+args.release_name)
    assert not temporary.exists()
    os.symlink(release,temporary);os.replace(temporary,BASE/'current')
    try:
        run(['systemctl','restart','zhixuan'])
        wait_ready('http://127.0.0.1:8600')
        check=runtime_check('http://127.0.0.1:8600')
        assert run(['systemctl','is-active','zhixuan'])=='active'
        assert run(['systemctl','is-active','nginx'])=='active'
        assert all(sha(Path(path))==digest for path,digest in report['configs'].items())
        report.update(phase='activated',live_runtime=check)
        marker.write_text(json.dumps(report,indent=2))
        print(json.dumps({'phase':'activated','release':str(release),'previous':str(old),'runtime':check}))
    except Exception:
        rollback=BASE/('current.rollback.'+args.release_name)
        os.symlink(old,rollback);os.replace(rollback,BASE/'current')
        run(['systemctl','restart','zhixuan'])
        wait_ready('http://127.0.0.1:8600')
        print(json.dumps({'phase':'rolled_back','restored':str(old)}))
        raise


if __name__=='__main__':main()
