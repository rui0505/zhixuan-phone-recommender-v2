"""HTTPS acceptance using the Windows TLS transport, with certificate checks enabled."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
BASE='https://zhixuanji.cn'

def transfer(request,timeout=25):
    url=request.full_url if isinstance(request,urllib.request.Request) else request
    data=request.data if isinstance(request,urllib.request.Request) else None
    args=['curl.exe','--fail','--silent','--show-error','--connect-timeout','10','--max-time',str(timeout),url]
    if data is not None:args+=['--header','Content-Type: application/json','--data-binary','@-']
    return BytesIO(subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=timeout+5).stdout)

def get(path,body=None):
    with transfer(urllib.request.Request(BASE+path,data=None if body is None else json.dumps(body).encode())) as r:return r.read()

def check_file(path):
    relative=path.relative_to(ROOT/'frontend').as_posix()
    content=get('/' if relative=='index.html' else '/'+relative)
    digest=hashlib.sha256(content).hexdigest()
    assert digest==hashlib.sha256(path.read_bytes()).hexdigest(),relative
    return {'path':relative,'bytes':len(content),'sha256':digest}

meta=json.loads(get('/api/v1/meta'))
assert meta['defaults']['evaluation_mode']=='spec_reference'
assert meta['reference_rule_version']=='spec-reference-1.0.0-774e84a695b7'
assert sum(x['count'] for x in meta['source_summary'])==477
files=[ROOT/'frontend/index.html',*sorted((ROOT/'frontend').glob('*.mjs')),*sorted((ROOT/'frontend').glob('*.css')),
       *sorted((ROOT/'frontend/assets').glob('*.png')),*sorted((ROOT/'frontend/assets/icons').glob('*.svg'))]
with ThreadPoolExecutor(max_workers=3) as pool:resources=list(pool.map(check_file,files))
urllib.request.urlopen=transfer
from api_smoke import smoke
acceptance=smoke(BASE)
body={**meta['defaults'],'needs':{'budget':{'min':None,'max':3000},'min_storage':256,'brands':{'include':[],'exclude':[]}},
      'method':'hybrid_rrf','confirmed':True,'expected_release':meta['release'],
      'expected_reference_rule_version':meta['reference_rule_version']}
ranked=json.loads(get('/api/v1/recommend?page_size=3',body))
assert ranked['status']=='ready' and ranked['ranked_total']==20
assert all(len(card['reference_scores'])==7 for card in ranked['ranked'])
reasons=[r['summary'] for r in ranked['recommendation_reasons'].values()]
assert all('/100' not in summary for summary in reasons)
reviews=json.loads(get('/api/v1/skus/S0462/reviews?page_size=20'))
assert reviews['sample_total']==132 and reviews['model']['model_id']=='M0140'
local_reviews=json.loads((ROOT/'data/buyer_reviews.json').read_text(encoding='utf-8'))
assert reviews['items']==local_reviews['models']['M0140'][:20]
bad=json.loads(get('/api/v1/skus/S0462/reviews?grade=bad&page_size=20'))
assert bad['total']==19 and all(r['grade']=='bad' for r in bad['items'])
next_page=json.loads(get('/api/v1/skus/S0462/reviews?page=2&page_size=20'))
assert not {r['id'] for r in reviews['items']} & {r['id'] for r in next_page['items']}
report={'review_dataset_version':reviews['dataset_version'],'sample_model':reviews['model']['display_name'],'sample_total':reviews['sample_total'],'sample_grade_counts':reviews['grade_counts'],'source_nicknames_and_contents_match':True,'pagination_disjoint':True,'transport':'curl.exe Windows TLS, certificate verification enabled','public_url':BASE+'/',
        'ready':json.loads(get('/readyz')),'mode':meta['defaults']['evaluation_mode'],
        'rule':meta['reference_rule_version'],'resources':resources,'exports':acceptance,
        'budget3000_storage256_ranked_total':ranked['ranked_total'],'customer_reasons':reasons}
output=ROOT/'local_evidence/buyer-reviews-public-acceptance-20261027.json'
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':'PASS','public_url':BASE+'/','resources_verified':len(resources),
                 'exports':acceptance['formats'],'default_ranked':acceptance['ranked_total'],
                 'budget3000_storage256':ranked['ranked_total'],'evidence':str(output)},ensure_ascii=False))
