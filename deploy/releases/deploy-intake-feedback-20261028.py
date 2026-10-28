"""Pinned one-release operator: isolated verification, reversible config patch, atomic activation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import time
import urllib.error
import urllib.request
import zipfile

BASE=Path('/opt/zhixuan')
PYTHON=BASE/'.venv/bin/python'
OLD=BASE/'releases/alpine-ui-20261027-buyerreviews'
NAME='intake-feedback-20261028-r1'
NEW=BASE/'releases'/NAME
BACKUP=Path('/root/zhixuan-intake-feedback-20261028-backup')
DROPDIR=Path('/etc/systemd/system/zhixuan.service.d')
DROPIN=DROPDIR/'feedback.conf'
ARCHIVE_SHA='6747a11a5af8d0c51db835847efeb051749e59a19a896715958eb2b1ba8791a4'
SNAPSHOT='128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8'
MODEL_SHA='bc169c68875090dc113c628d95162267bdbf6b7bcca0192eb628dced98304b03'
TEST_KEY='ISOLATED-DEPLOYMENT-FIXTURE-0000000000000000'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def run(args,**kwargs):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=60,**kwargs).stdout.strip()
def get(base,path,body=None,method=None,headers=None,expected=200):
    request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json',**(headers or {})},method=method)
    try:
        with urllib.request.urlopen(request,timeout=20) as response:
            assert response.status==expected,(path,response.status,expected)
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code!=expected:raise
        return json.load(error)

def ready(base):
    end=time.monotonic()+25
    while time.monotonic()<end:
        try:return get(base,'/readyz')
        except Exception:time.sleep(.3)
    raise RuntimeError('Readiness timeout')

def runtime(base,fixture=False):
    meta=get(base,'/api/v1/meta');assert meta['release']['snapshot_sha256']==SNAPSHOT
    structure=meta['needs_semantic_structure']
    assert len(structure['nodes'])==7 and structure['model_sha256']==MODEL_SHA
    assert not structure['empirically_validated']
    catalog=get(base,'/api/v1/catalog?mode=all&page_size=24')
    assert catalog['total']==484 and meta['counts']['phone_models']==147 and meta['counts']['skus']==484
    body={**meta['defaults'],'needs':{'budget':{'min':None,'max':None},'min_storage':None,'brands':{'include':[],'exclude':[]}},
        'method':'hybrid_rrf','confirmed':True,'expected_release':meta['release']}
    ranked=get(base,'/api/v1/recommend?page_size=3',body)
    assert ranked['status']=='ready' and ranked['ranked_total']==40
    reviews=get(base,'/api/v1/skus/S0462/reviews?page_size=20')
    assert reviews['sample_total']==132
    with urllib.request.urlopen(base+'/',timeout=20) as response:html=response.read().decode()
    assert '数据说明/反馈' in html and 'id="usage-feedback"' in html and 'id="feedback-form"' in html
    public=get(base,'/api/v1/feedback');assert all(set(item)=={'id','nickname','rating','category','page','content','created_at','published_at'} for item in public['items'])
    get(base,'/api/v1/admin/feedback',expected=401)
    get(base,'/api/v1/intake/queue',expected=401)
    with urllib.request.urlopen(base+'/admin.html',timeout=20) as response:assert '机型管理' in response.read().decode()
    if fixture:
        from uuid import uuid4
        draft={'submission_id':str(uuid4()),'nickname':'隔离发布测试','rating':4,'category':'suggestion','page':'data','content':'隔离环境测试评论，绝不写入生产反馈库。'}
        receipt=get(base,'/api/v1/feedback',draft,expected=201)
        assert receipt['status']=='pending' and get(base,'/api/v1/feedback')['total']==0
        assert get(base,'/api/v1/feedback',draft)['id']==receipt['id']
        auth={'Authorization':'Bearer '+TEST_KEY}
        assert get(base,'/api/v1/admin/feedback',headers=auth)['counts']['pending']==1
        get(base,'/api/v1/admin/feedback/'+receipt['id'],{'status':'published'},method='PATCH',headers=auth)
        assert get(base,'/api/v1/feedback')['total']==1
        get(base,'/api/v1/admin/feedback/'+receipt['id'],{'status':'rejected'},method='PATCH',headers=auth)
        assert get(base,'/api/v1/feedback')['total']==0
        intake={'model':{'brand':'隔离发布测试','display_name':'隔离新机（非真实）','release_month':'2026-10','source_url':'https://example.org/spec'},
            'variant':{'variant_name':'标准版','region':None,'satellite_support':False,'is_default':True},
            'skus':[{'ram_gb':12,'storage_gb':256,'memory_type':None,'price':{'amount':2999,'price_kind':'official_current_price','condition':'new','observed_at':'2026-10-28','source_url':'https://example.org/price','source_note':''}}],
            'specifications':[{'key':k,'value_num':v,'unit':u,'raw_value':raw} for k,v,u,raw in [('weight_g',190,'g','190g'),('battery_capacity_mah',6000,'mAh','6000mAh'),('screen_size_inch',6.5,'英寸','6.5英寸'),('chipset',None,None,'A18'),('rear_camera',None,None,'5000万像素主摄 + 超广角 + 长焦')]],'source_note':'仅隔离发布验证，不写入生产。'}
        queued=get(base,'/api/v1/intake/submit',intake,headers=auth,expected=201)['record']
        activated=get(base,'/api/v1/intake/'+queued['record_id']+'/publish',{'expected_revision':queued['revision'],'expected_release':meta['release'],'sources_verified':True},headers=auth)
        assert activated['record']['status']=='published'
        assert get(base,'/api/v1/catalog?mode=all')['total']==485
        assert get(base,'/api/v1/meta')['counts']['phone_models']==148
        assert get(base,'/api/v1/skus/S0462/reviews')['sample_total']==132
        with urllib.request.urlopen(base+'/media/'+ranked['ranked'][0]['image']['url'].rsplit('/',1)[-1],timeout=20) as response:assert response.read(4)==b'RIFF'
    return {'models':147,'configurations':484,'ranked_total':40,'buyer_review_sample':132,
        'aism_model_sha256':structure['model_sha256'],'public_feedback_count':public['total'],
        'ranking_sha256':ranked['ranking_sha256'],'decision_sha256':ranked['decision_sha256']}

def manifest_check():
    manifest=json.loads((NEW/'bundle_manifest.json').read_text())
    for name,digest in manifest['files'].items():assert sha(NEW/name)==digest,name
    assert sha(NEW/'data/catalog.sqlite')==SNAPSHOT
    assert not any(name.startswith('local_state/') or name.endswith(('.key','.sqlite3')) for name in manifest['files'])
    return manifest

def patch_nginx(text):
    assert 'proxy_pass http://127.0.0.1:8600;' in text
    assert 'proxy_set_header' not in text,'Existing proxy headers require review'
    # Add only inherited proxy headers. Listen addresses, redirects and TLS stay byte-for-byte otherwise.
    patched,count=re.subn(r'(?m)^(\s*)server\s*\{',lambda m:m.group(0)+'\n'+m[1]+'    proxy_set_header Host $host;\n'+m[1]+'    proxy_set_header X-Forwarded-For $remote_addr;\n'+m[1]+'    proxy_set_header X-Forwarded-Proto $scheme;',text)
    assert count>0
    return patched

def prepare(archive):
    assert not NEW.exists(),'Fresh release directory required'
    assert not DROPIN.exists(),'Unexpected existing feedback drop-in'
    configs=[]
    for path in Path('/etc/nginx/sites-enabled').iterdir():
        if path.is_file() and 'proxy_pass http://127.0.0.1:8600;' in path.read_text():
            actual=path.resolve();assert actual.parent==Path('/etc/nginx/sites-available')
            patch_nginx(actual.read_text())
            if actual not in configs:configs.append(actual)
    assert configs,'No verified Zhixuan reverse proxy found'
    assert run(['systemctl','is-active','zhixuan'])=='active'
    assert run(['systemctl','is-active','nginx'])=='active'
    BACKUP.mkdir(mode=0o700,exist_ok=True)
    NEW.mkdir()
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            path=Path(item.filename)
            assert not path.is_absolute() and '..' not in path.parts and not stat.S_ISLNK(item.external_attr>>16)
        package.extractall(NEW)
    manifest=manifest_check()
    for path in NEW.rglob('*'):path.chmod(0o755 if path.is_dir() else 0o644)
    NEW.chmod(0o755)
    with sqlite3.connect((NEW/'data/catalog.sqlite').as_uri()+'?mode=ro',uri=True) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    immutable=[Path('/etc/systemd/system/zhixuan.service'),Path('/etc/zhixuan.env')]
    immutable_hashes={str(path):sha(path) for path in immutable if path.exists()}
    config_hashes={str(path):sha(path) for path in configs}
    for index,path in enumerate(configs):shutil.copy2(path,BACKUP/f'nginx-{index}.conf')
    env={**os.environ,'PHONE_ENABLE_LLM':'false','LLM_API_KEY':'','LLM_ENV_FILE':'/not-configured-in-isolated-staging',
        'PHONE_FEEDBACK_DB':str(BACKUP/'staging-feedback.sqlite3'),'PHONE_FEEDBACK_ADMIN_TOKEN':TEST_KEY}
    env.pop('PYTHONPATH',None);env.pop('PHONE_CATALOG_PATH',None)
    with socket.socket() as probe:probe.bind(('127.0.0.1',18671))
    with (BACKUP/'staging.log').open('w') as log:
        process=subprocess.Popen([str(PYTHON),'-m','uvicorn','api.app:app','--host','127.0.0.1','--port','18671','--log-level','error'],cwd=NEW,env=env,stdout=log,stderr=log)
        try:
            ready('http://127.0.0.1:18671');assert process.poll() is None
            smoke=json.loads(run([str(PYTHON),'scripts/api_smoke.py','--base','http://127.0.0.1:18671'],cwd=NEW,env=env))
            check=runtime('http://127.0.0.1:18671',fixture=True)
        finally:process.terminate();process.wait(timeout=10)
    assert all(sha(Path(p))==h for p,h in {**immutable_hashes,**config_hashes}.items())
    report={'phase':'verified','previous':str(OLD),'release':str(NEW),'archive_sha256':ARCHIVE_SHA,
        'files':len(manifest['files']),'immutable_configs':immutable_hashes,'nginx_configs':config_hashes,'runtime':check,'smoke':smoke}
    (BACKUP/'release.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'phase':'verified','release':str(NEW),'files':len(manifest['files']),'runtime':check,'smoke':smoke}))

def switch(target,label):
    temporary=BASE/('current.'+label+'.'+NAME)
    assert not temporary.exists() and not temporary.is_symlink()
    os.symlink(target,temporary);os.replace(temporary,BASE/'current')

def activate():
    report=json.loads((BACKUP/'release.json').read_text())
    assert report['phase']=='verified' and report['archive_sha256']==ARCHIVE_SHA
    assert not DROPIN.exists()
    manifest_check()
    assert all(sha(Path(p))==h for p,h in {**report['immutable_configs'],**report['nginx_configs']}.items())
    assert not Path('/var/lib/zhixuan/feedback.sqlite3').exists(),'Unexpected production feedback; inspect before proceeding'
    configured=False;switched=False
    try:
        DROPDIR.mkdir(exist_ok=True)
        with DROPIN.open('x') as stream:stream.write('[Service]\nStateDirectory=zhixuan\nStateDirectoryMode=0700\nEnvironment=PHONE_FEEDBACK_DB=/var/lib/zhixuan/feedback.sqlite3\n')
        DROPIN.chmod(0o644);configured=True
        for path in report['nginx_configs']:
            target=Path(path);target.write_text(patch_nginx(target.read_text()))
        run(['nginx','-t']);run(['systemctl','daemon-reload'])
        switch(NEW,'next');switched=True
        run(['systemctl','restart','zhixuan']);ready('http://127.0.0.1:8600')
        run(['systemctl','reload','nginx'])
        check=runtime('http://127.0.0.1:8600')
        assert run(['systemctl','is-active','zhixuan'])=='active' and run(['systemctl','is-active','nginx'])=='active'
        assert all(sha(Path(p))==h for p,h in report['immutable_configs'].items())
        assert Path('/var/lib/zhixuan/feedback.sqlite3').is_file()
        assert Path('/var/lib/zhixuan/feedback-admin.key').is_file()
        private_key=Path('/var/lib/zhixuan/feedback-admin.key').read_text().strip()
        queue=get('http://127.0.0.1:8600','/api/v1/intake/queue',headers={'Authorization':'Bearer '+private_key})
        assert queue['total']==0, 'Unexpected production intake records; inspect before continuing'
        assert Path('/var/lib/zhixuan/intake.sqlite3').is_file()
        assert not (NEW/'local_state').exists(),'Mutable feedback escaped persistent state directory'
        report.update(phase='activated',live_runtime=check,feedback_db='/var/lib/zhixuan/feedback.sqlite3',dropin_sha256=sha(DROPIN),activated_nginx={p:sha(p) for p in report['nginx_configs']})
        (BACKUP/'release.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({'phase':'activated','release':str(NEW),'previous':str(OLD),'feedback_db':report['feedback_db'],'runtime':check}))
    except Exception:
        if switched:switch(OLD,'rollback')
        if configured:
            DROPIN.unlink(missing_ok=True)
            for index,path in enumerate(report['nginx_configs']):shutil.copy2(BACKUP/f'nginx-{index}.conf',path)
            run(['systemctl','daemon-reload']);run(['nginx','-t']);run(['systemctl','reload','nginx'])
        if switched:run(['systemctl','restart','zhixuan']);ready('http://127.0.0.1:8600')
        print(json.dumps({'phase':'rolled_back','restored':str(OLD)}));raise

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['verify','activate'],required=True);parser.add_argument('--archive',required=True);args=parser.parse_args()
    archive=Path(args.archive).resolve()
    assert os.geteuid()==0 and socket.gethostname()=='iZj6c8jqxomk685dgmtuc2Z'
    assert archive.parent==Path('/tmp') and sha(archive)==ARCHIVE_SHA and PYTHON.is_file()
    assert (BASE/'current').resolve()==OLD and sha(OLD/'data/catalog.sqlite')==SNAPSHOT
    if args.phase=='verify':prepare(archive)
    else:activate()

if __name__=='__main__':main()
