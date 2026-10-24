"""Build an owner-approved display overlay without changing the numeric release.

Exact (brand, display_name) joins only. No fuzzy matching or old-ID reuse.
Pillow is a build-time dependency, not an API runtime dependency.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3


def read_rows(path,query):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        return [dict(row) for row in db.execute(query)]


def publish(source_db,assets_root,target_db,output_root,*,approved=False):
    if not approved:raise ValueError('Explicit owner approval for public display required')
    from PIL import Image
    source=read_rows(source_db,'SELECT model_id,brand,display_name FROM phone_models')
    target=read_rows(target_db,'SELECT model_id,brand,display_name FROM phone_models ORDER BY model_id')
    assets=read_rows(source_db,'SELECT asset_id,model_id,derived_path,color_name,is_cover FROM assets ORDER BY is_cover DESC,asset_id')
    counts=Counter((r['brand'],r['display_name']) for r in source)
    target_counts=Counter((r['brand'],r['display_name']) for r in target)
    exact={(r['brand'],r['display_name']):r['model_id'] for r in source if counts[(r['brand'],r['display_name'])]==1}
    base=Path(assets_root).resolve(); out=Path(output_root).resolve(); media=out/'media'; media.mkdir(parents=True,exist_ok=True)
    result={'approval':'OWNER_APPROVED_PUBLIC_DISPLAY','approval_date':'2026-10-07',
            'approval_note':'Project owner confirmed public display in chat; not an independent copyright certification.',
            'snapshot_sha256':hashlib.sha256(Path(target_db).read_bytes()).hexdigest(),
            'association_rule':'Exact brand and display_name; never reuse source model IDs across releases.',
            'models':{},'missing_models':[]}
    for model in target:
        key=(model['brand'],model['display_name']); old=exact.get(key) if target_counts[key]==1 else None
        chosen=None
        for asset in assets:
            if old is None or asset['model_id']!=old:continue
            path=(base/asset['derived_path']).resolve()
            if not path.is_relative_to(base) or path.is_symlink() or path.suffix.lower()!='.webp' or not path.is_file():continue
            try:
                with Image.open(path) as image:image.load(); dimensions=list(image.size)
            except (OSError,ValueError):continue
            chosen=(asset,path,dimensions); break
        if chosen is None:result['missing_models'].append(model['model_id']); continue
        if not re.fullmatch(r'[A-Za-z0-9_-]+',model['model_id']):raise ValueError('Unsafe model ID')
        asset,path,dimensions=chosen; digest=hashlib.sha256(path.read_bytes()).hexdigest()
        filename=f"{model['model_id']}_{digest[:16]}.webp"
        shutil.copyfile(path,media/filename)
        result['models'][model['model_id']]={'filename':filename,'sha256':digest,'color':asset['color_name'],
            'alt':f"{model['display_name']} · {asset['color_name'] or '机型展示'}",'dimensions':dimensions}
    (out/'image_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--source-db',required=True); parser.add_argument('--assets-root',required=True)
    parser.add_argument('--target-db',required=True); parser.add_argument('--output-root',required=True); parser.add_argument('--owner-approved',action='store_true')
    args=parser.parse_args(); result=publish(args.source_db,args.assets_root,args.target_db,args.output_root,approved=args.owner_approved)
    print(json.dumps({'published_models':len(result['models']),'missing_models':result['missing_models'],'snapshot_sha256':result['snapshot_sha256']},ensure_ascii=False))
