"""Read-only release gate, usable without the private inputs."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.export_snapshot import ALLOWLIST,sha,validate_text


def check_release(root: Path) -> dict:
    root=Path(root); path=root/'data/catalog.sqlite'; errors=[]
    try:
        manifest=json.loads((root/'data/release_manifest.json').read_text(encoding='utf-8'))
        if sha(path)!=manifest['snapshot_sha256']: errors.append('snapshot_hash_mismatch')
        if manifest['status']!='PASS' or manifest['acceptance']['machine_acceptance']!='PASS': errors.append('machine_not_passed')
        if manifest.get('llm_enabled') is not False: errors.append('paid_model_default_enabled')
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok': errors.append('integrity_failed')
            if c.execute('PRAGMA foreign_key_check').fetchall(): errors.append('foreign_key_violation')
            for table, in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                rows=c.execute('SELECT * FROM '+table).fetchall()
                if table not in ALLOWLIST and rows: errors.append('non_allowlisted_table:'+table)
                if table in ALLOWLIST and len(rows)!=manifest['table_counts'][table]: errors.append('count_mismatch:'+table)
                columns=[x[1] for x in c.execute('PRAGMA table_info('+table+')')]
                for row in rows:
                    if table in ALLOWLIST and any(v is not None for k,v in zip(columns,row) if k not in ALLOWLIST[table].split()): errors.append('non_allowlisted_column:'+table)
                    for value in row: validate_text(value)
            if c.execute('SELECT count(*) FROM phone_models WHERE cover_asset_id IS NOT NULL').fetchone()[0]: errors.append('unpublished_image_reference')
        return {'status':'FAIL' if errors else 'PASS','errors':errors,'snapshot_sha256':sha(path),
                'acceptance':manifest['acceptance'],'images':manifest['images']}
    except (OSError,ValueError,KeyError,sqlite3.Error):
        return {'status':'FAIL','errors':['release_invalid_or_missing']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='.'); args=parser.parse_args()
    result=check_release(Path(args.root)); print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='PASS' else 1)
