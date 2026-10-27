"""Package only public runtime files and approved display images; no Git/secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

def build(root,output):
    root=Path(root).resolve(); output=Path(output).resolve()
    names=['bridge.py','engine.py','ranking_modes.py','schemas.py','needs_extraction.py','semantic_aism.py','data/aism-semantic-structure.json','requirements-api.txt','scripts/api_smoke.py',
           'ui/__init__.py','ui/export_results.py','data/catalog.sqlite','data/release_manifest.json','data/image_manifest.json',
           'data/chipset-tiers.json','data/chipset-primary-reference.json','data/buyer_reviews.json',
           'frontend/assets/zhixuan-ai-network.jpg','frontend/assets/alpine-panorama.png',
           'frontend/assets/workflow-illustration.png','frontend/assets/icons/LICENSE.txt','frontend/assets/credits.json']
    names.extend(str(p.relative_to(root)).replace('\\','/') for p in (root/'frontend/assets/icons').glob('*.svg'))
    for folder in ('api','catalog','providers'):
        names.extend(str(p.relative_to(root)).replace('\\','/') for p in (root/folder).glob('*.py'))
    for folder,pattern in (('frontend','*.mjs'),('frontend','*.html'),('frontend','*.css'),('deploy','*.sh'),('deploy','*.service'),('deploy','*.conf')):
        names.extend(str(p.relative_to(root)).replace('\\','/') for p in (root/folder).glob(pattern))
    manifest=json.loads((root/'data/image_manifest.json').read_text(encoding='utf-8'))
    if manifest['approval']!='OWNER_APPROVED_PUBLIC_DISPLAY':raise ValueError('Images not approved')
    if hashlib.sha256((root/'data/catalog.sqlite').read_bytes()).hexdigest()!=manifest['snapshot_sha256']:raise ValueError('Images belong to another release')
    for image in manifest['models'].values():
        filename=image['filename']
        if Path(filename).name!=filename or not filename.endswith('.webp'):raise ValueError('Unsafe image path')
        path=root/'data/media'/filename
        if hashlib.sha256(path.read_bytes()).hexdigest()!=image['sha256']:raise ValueError('Image changed')
        names.append('data/media/'+filename)
    names=sorted(set(names)); hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as package:
        for name in names:
            path=root/name
            if path.is_symlink():raise ValueError('Symlink not allowed')
            package.write(path,name)
        package.writestr('bundle_manifest.json',json.dumps({'files':hashes,'image_models':len(manifest['models'])},indent=2))
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='.');parser.add_argument('--output',required=True)
    args=parser.parse_args(); path=build(args.root,args.output)
    print(json.dumps({'file':str(path),'size_bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
