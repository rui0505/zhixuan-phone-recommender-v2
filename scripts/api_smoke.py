"""Real TCP checks usable in an API-only Python environment, without credentials."""
import argparse
import csv
from io import BytesIO,StringIO
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def smoke(base):
    def call(path,body=None):
        req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),
                                   headers={} if body is None else {'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=20) as response:return response.read()
    meta=json.loads(call('/api/v1/meta')); catalog=json.loads(call('/api/v1/catalog?mode=new'))
    body={**meta['defaults'],'needs':{'budget':{'min':None,'max':None},'min_storage':None,'brands':{'include':[],'exclude':[]}},
          'confirmed':True,'expected_release':meta['release']}
    ranked=json.loads(call('/api/v1/recommend',body)); assert ranked['status']=='ready'
    export_body={'request':body,'fingerprints':{k:ranked[k] for k in ('decision_sha256','ranking_sha256')}}
    exports={fmt:call('/api/v1/export/'+fmt,export_body) for fmt in ('csv','xlsx','json')}
    rows=list(csv.DictReader(StringIO(exports['csv'].decode('utf-8-sig')))); document=json.loads(exports['json'])
    from openpyxl import load_workbook
    wb=load_workbook(BytesIO(exports['xlsx']),read_only=True); xlsx=list(wb['推荐结果'].values); wb.close()
    assert len(rows)==len(document['ranking'])==len(xlsx)-1==ranked['ranked_total']
    assert rows[0]['ranking_sha256']==document['evidence']['ranking_sha256']==ranked['ranking_sha256']
    assert [r['sku_id'] for r in rows]==[r['sku_id'] for r in document['ranking']]
    if ranked['ranked'][0].get('image'):
        image=call(ranked['ranked'][0]['image']['url']); assert image[:4]==b'RIFF' and image[8:12]==b'WEBP'
    import importlib
    importlib.import_module('api.app')
    assert not {'streamlit','web.server','data_pipeline','docx'}.intersection(sys.modules)
    return {'ranked_total':ranked['ranked_total'],'catalog_new':catalog['total'],'formats':list(exports),
            'release':meta['release'],'decision_sha256':ranked['decision_sha256'],'ranking_sha256':ranked['ranking_sha256']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--self-host',action='store_true');parser.add_argument('--base',default='http://127.0.0.1:8600')
    args=parser.parse_args(); process=None
    try:
        if args.self_host:
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            args.base=f'http://127.0.0.1:{port}'
            process=subprocess.Popen([sys.executable,'-m','uvicorn','api.app:app','--host','127.0.0.1','--port',str(port),'--log-level','error'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            until=time.monotonic()+25
            while True:
                try:
                    with urllib.request.urlopen(args.base+'/readyz',timeout=1) as response:assert response.status==200
                    break
                except Exception:
                    if process.poll() is not None:raise RuntimeError(process.stderr.read().decode(errors='replace'))
                    if time.monotonic()>until:raise TimeoutError('API readiness timeout')
                    time.sleep(.1)
        print(json.dumps(smoke(args.base),ensure_ascii=True))
    finally:
        if process:
            process.terminate();process.wait(timeout=10);process.stderr.close()
