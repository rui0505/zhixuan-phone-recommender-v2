"""Offline allowlist exporter. Never copy the full database or write the sources."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import shutil
import tempfile
from contextlib import closing
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
ALLOWLIST={
    'phone_models':'model_id brand display_name display_name_match_key release_month release_granularity first_source_record_id',
    'phone_variants':'variant_id model_id variant_name name_is_source region satellite_support is_default source_record_id',
    'skus':'sku_id variant_id model_id ram_raw ram_gb storage_raw storage_gb memory_type source_record_id',
    'price_snapshots':'price_id sku_id amount currency price_raw price_kind condition is_estimated channel price_note qualifiers source_sale_status observed_at observed_at_kind source_id source_record_id',
    'specifications':'spec_id subject_type subject_id variant_id spec_key unit value_num value_text raw_value raw_value_type source_col source_record_id source_id parse_status comparison_group rule_version note',
    'sources':'source_id source_kind url url_count publisher published_at published_granularity collected_at url_is_parseable',
    'spec_provenance':'spec_id source_record_id source_col source_id',
    'benchmark_runs':'run_id subject_type subject_id model_id variant_id sku_id benchmark institution protocol protocol_detail conditions_json run_date os_version comparison_group applicable_scope scope_note source_id source_record_id',
    'metric_observations':'observation_id run_id metric_key value_num unit raw_value_type missing_reason source_col source_record_id comparison_group verification_status',
    'metric_definitions':'metric_key name_cn unit direction comparability_prefix value_type range_rule cross_source_comparable',
    'dataset_releases':'release_id dataset_version rule_version id_registry_version input_hashes_json built_at',
    'field_dictionary':'field_name layer unit description',
}
TECHNICAL_SPECS={'battery_capacity_mah','chipset','display_peak_brightness_nit','front_camera',
    'has_headphone_jack','has_ir_blaster','has_nfc','has_wireless_charge','rear_camera','refresh_rate_hz',
    'resolution','screen_form','screen_material','screen_size_inch','speaker_config','weight_g',
    'wired_charge_w','wireless_charge_w'}
PRIVATE=re.compile(r'(?i)(?:(?<![a-z0-9])[a-z]:[\\/]|(?:^|\s)\\\\|/Users/|/home/|sk-[A-Za-z0-9]{12,}|(?:api[_ -]?key|password|access_token)\s*[=:])')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_url(value):
    if not value: return None
    try:
        url=urlsplit(value)
        return value if url.scheme in ('https','http') and url.hostname and not url.username and not url.password else None
    except ValueError:
        return None


def validate_text(value):
    if isinstance(value,str) and (PRIVATE.search(value) or '\x00' in value or '\ufffd' in value or len(value)>2048):
        raise ValueError('Publishable field contains a private path, secret pattern, invalid text or long excerpt')


def schema():
    tree=ast.parse((ROOT/'data_pipeline/src/stage_db.py').read_text(encoding='utf-8'))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='DDL' for t in n.targets))


def export_snapshot(source_db: Path, destination_db: Path, *, approved_asset_ids: set[str], release_status: dict) -> dict:
    source_db=Path(source_db).resolve(); destination_db=Path(destination_db).resolve()
    if source_db==destination_db: raise ValueError('Source and release must differ')
    if release_status.get('machine_acceptance')!='PASS': raise ValueError('Machine acceptance required before export')
    if approved_asset_ids: raise ValueError('Image export is not implemented; reviewed rights and a new release are required')
    destination_db.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='release-',dir=destination_db.parent) as temp:
        candidate=Path(temp)/'catalog.sqlite'
        with closing(sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(candidate)) as dst:
            src.row_factory=sqlite3.Row; dst.executescript(schema()); counts={}; compared=0
            for table,columns in ALLOWLIST.items():
                fields=columns.split(); allrows=[]
                for row in src.execute('SELECT * FROM '+table):
                    if table=='specifications' and row['spec_key'] not in TECHNICAL_SPECS: continue
                    if table=='spec_provenance' and not dst.execute('SELECT 1 FROM specifications WHERE spec_id=?',(row['spec_id'],)).fetchone(): continue
                    values={k:row[k] for k in fields}
                    if table=='specifications' and row['spec_key']!='display_peak_brightness_nit': values['note']=None
                    if table=='sources': values['url']=safe_url(values['url'])
                    for v in values.values(): validate_text(v)
                    dst.execute(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",tuple(values.values()))
                    allrows.append(tuple(values.values()))
                actual=dst.execute(f"SELECT {','.join(fields)} FROM {table}").fetchall()
                if actual!=allrows: raise ValueError('Release business values differ from allowlisted source values')
                counts[table]=len(actual); compared+=len(actual)
            # Provenance for deliberately excluded text-only specs must not dangle.
            dst.execute('DELETE FROM spec_provenance WHERE spec_id NOT IN (SELECT spec_id FROM specifications)')
            counts['spec_provenance']=dst.execute('SELECT count(*) FROM spec_provenance').fetchone()[0]
            violations=dst.execute('PRAGMA foreign_key_check').fetchall()
            if violations: raise ValueError('Release foreign key violation')
            dst.commit()
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Release integrity failure')
        manifest={'status':'PASS','snapshot_sha256':sha(candidate),'dataset_version':None,'feature_rule_version':None,
            'allowlist':{t:c.split() for t,c in ALLOWLIST.items()},'table_counts':counts,
            'business_comparison':{'status':'PASS','compared_rows':compared,'numeric_values_changed':0},
            'acceptance':{k:release_status.get(k,'NOT_CHECKED') for k in ('machine_acceptance','overall_acceptance','wps_review')},
            'images':{'published_count':0,'status':'NOT_PUBLISHED_RIGHTS_PENDING'},'long_excerpts':'NOT_PUBLISHED',
            'llm_enabled':False,'vikor':'NOT_IMPLEMENTED','python_version':'3.12'}
        with closing(sqlite3.connect(candidate)) as c:
            version=c.execute('select dataset_version,rule_version from dataset_releases limit 1').fetchone()
            if version: manifest['dataset_version'],manifest['feature_rule_version']=version
        for value in manifest['acceptance'].values(): validate_text(value)
        # Failed validations above cannot overwrite the last known release.
        manifest_path=destination_db.with_name('release_manifest.json')
        staging=Path(temp)/'manifest.json'
        staging.write_text(json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        backup=Path(temp)/'previous.sqlite'
        if destination_db.exists(): shutil.copy2(destination_db,backup)
        os.replace(candidate,destination_db)
        try:
            os.replace(staging,manifest_path)
        except OSError:
            if backup.exists(): os.replace(backup,destination_db)
            else: destination_db.unlink()
            raise
        return manifest


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--source',required=True)
    parser.add_argument('--destination',default='data/catalog.sqlite'); parser.add_argument('--report',required=True)
    args=parser.parse_args(); report=json.loads(Path(args.report).read_text(encoding='utf-8'))
    result=export_snapshot(Path(args.source),Path(args.destination),approved_asset_ids=set(),release_status=report)
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__': main()
