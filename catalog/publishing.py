"""Append reviewed products to a new immutable snapshot, then atomically select it."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import uuid4

from api.contracts import ApiError
from .release import file_sha, verify_release


def stamp():return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
def model_id(record_id):return 'IM_'+record_id.replace('-','')
def record_origin(record):
    digest=hashlib.sha256(json.dumps(record['payload'],sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')).hexdigest()
    return 'intake:'+record['record_id']+':'+digest
def insert(con,table,**values):
    con.execute('INSERT INTO '+table+' ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))
def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')


def build_snapshot(record,store,release):
    root=store.active_root
    if root is None:raise ApiError(503,'intake_disabled','当前服务未配置持久目录，不能发布')
    source=Path(release.repo.db_path);data_root=source.parent
    snapshots=root/'snapshots';snapshots.mkdir(parents=True,mode=0o700,exist_ok=True)
    directory='r-'+uuid4().hex;target=snapshots/directory;target.mkdir(mode=0o700)
    database=target/'catalog.sqlite';body=record['payload'];rid=record['record_id'];mid=model_id(rid);vid='IV_'+rid.replace('-','')
    now=stamp();origin=record_origin(record)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(database)) as con:
        src.backup(con);con.execute('PRAGMA foreign_keys=ON')
        insert(con,'phone_models',model_id=mid,brand=body['model']['brand'],display_name=body['model']['display_name'],
               display_name_match_key=body['model']['display_name'].casefold().replace(' ',''),release_month=body['model']['release_month'],
               release_granularity='month',first_source_record_id=origin)
        variant=body['variant']
        insert(con,'phone_variants',variant_id=vid,model_id=mid,variant_name=variant['variant_name'],name_is_source=1,
               region=variant['region'],satellite_support=int(variant['satellite_support']),is_default=int(variant['is_default']),source_record_id=origin)
        spec_source='ISOURCE_'+rid.replace('-','')
        if body['specifications']:
            insert(con,'sources',source_id=spec_source,source_kind='spec',url=body['model']['source_url'],url_count=1,
                   publisher=body['model']['brand'],collected_at=now,url_is_parseable=1)
        for index,sku in enumerate(body['skus']):
            sid='ISKU_'+rid.replace('-','')+'_'+str(index);price=sku['price'];price_source='IPRICE_SOURCE_'+rid.replace('-','')+'_'+str(index)
            insert(con,'skus',sku_id=sid,variant_id=vid,model_id=mid,ram_raw=str(sku['ram_gb'])+'GB',ram_gb=sku['ram_gb'],
                   storage_raw=str(sku['storage_gb'])+'GB',storage_gb=sku['storage_gb'],memory_type=sku['memory_type'],source_record_id=origin)
            insert(con,'sources',source_id=price_source,source_kind='price',url=price['source_url'],url_count=1,
                   publisher=body['model']['brand'],collected_at=now,url_is_parseable=1)
            insert(con,'price_snapshots',price_id='IP_'+rid.replace('-','')+'_'+str(index),sku_id=sid,amount=price['amount'],currency='CNY',
                   price_raw=str(price['amount']),price_kind=price['price_kind'],condition=price['condition'],
                   is_estimated=int(price['price_kind']=='used_market_estimate'),channel='admin_reviewed',price_note=price['source_note'],
                   observed_at=price['observed_at'],observed_at_kind='source_date',source_id=price_source,source_record_id=origin)
        for index,spec in enumerate(body['specifications']):
            specid='ISPEC_'+rid.replace('-','')+'_'+str(index)
            insert(con,'specifications',spec_id=specid,subject_type='variant',subject_id=vid,variant_id=vid,spec_key=spec['key'],
                   unit=spec['unit'],value_num=spec['value_num'],value_text=spec['raw_value'] if spec['value_num'] is None else None,
                   raw_value=spec['raw_value'],raw_value_type='str',source_col=spec['key'],source_record_id=origin,source_id=spec_source,
                   parse_status='ok' if spec['value_num'] is not None else 'non_numeric' if spec['key'] in {'chipset','rear_camera','front_camera','resolution','screen_form','screen_material','speaker_config'} else 'missing',comparison_group='spec::'+spec['key'],
                   rule_version=release.key['feature_rule_version'])
            insert(con,'spec_provenance',spec_id=specid,source_record_id=origin,source_col=spec['key'],source_id=spec_source)
        previous=release.repo.dataset_version();version=previous['dataset_version'].split('+intake-')[0]+'+intake-'+directory[2:14]
        con.execute('DELETE FROM dataset_releases')
        insert(con,'dataset_releases',release_id='INTAKE_'+directory[2:],dataset_version=version,rule_version=previous['rule_version'],
               id_registry_version=previous.get('id_registry_version'),input_hashes_json=json.dumps({'parent_snapshot':release.key['snapshot_sha256'],'intake_record':rid}),built_at=now)
        con.commit()
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('发布数据库校验失败')
        counts={table:con.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in release.manifest['table_counts']}
    manifest=deepcopy(release.manifest);digest=file_sha(database)
    manifest.update(snapshot_sha256=digest,dataset_version=version,table_counts=counts,intake_record_id=rid,
                    parent_snapshot_sha256=release.key['snapshot_sha256'])
    manifest['acceptance']={'machine_acceptance':'PASS','overall_acceptance':'PENDING','wps_review':'NOT_CHECKED'}
    manifest['business_comparison']={'status':'PASS','numeric_values_changed':0,'compared_rows':sum(counts.values()),'basis':'immutable parent plus reviewed append-only intake'}
    write_json(target/'release_manifest.json',manifest)
    for name in ('chipset-tiers.json','chipset-primary-reference.json'):
        if (data_root/name).is_file():shutil.copy2(data_root/name,target/name)
    # Keep the approved old media available without copying it into every snapshot.
    if (data_root/'image_manifest.json').is_file():
        images=json.loads((data_root/'image_manifest.json').read_text(encoding='utf-8'))
        if images.get('snapshot_sha256')!=release.key['snapshot_sha256']:raise ValueError('图片版本不一致')
        media=root/'media';media.mkdir(mode=0o700,exist_ok=True)
        source_media=data_root/'media' if (data_root/'media').is_dir() else media
        for item in images.get('models',{}).values():
            filename=item['filename'];origin_file=source_media/filename;dest=media/filename
            if Path(filename).name!=filename or origin_file.is_symlink() or file_sha(origin_file)!=item['sha256']:raise ValueError('图片文件无效')
            if not dest.exists():
                with tempfile.NamedTemporaryFile(dir=media,delete=False) as out:
                    temporary=Path(out.name)
                    with origin_file.open('rb') as incoming:shutil.copyfileobj(incoming,out)
                    out.flush();os.fsync(out.fileno())
                os.replace(temporary,dest)
            if file_sha(dest)!=item['sha256']:raise ValueError('持久图片文件变化')
        images['snapshot_sha256']=digest;write_json(target/'image_manifest.json',images)
    if (data_root/'buyer_reviews.json').is_file():
        reviews=json.loads((data_root/'buyer_reviews.json').read_text(encoding='utf-8'))
        if reviews.get('snapshot_sha256')!=release.key['snapshot_sha256']:raise ValueError('买家评价版本不一致')
        reviews['snapshot_sha256']=digest;write_json(target/'buyer_reviews.json',reviews)
    checked=verify_release(database,file_sha(target/'release_manifest.json'),digest)
    return directory,checked


def publish_snapshot(record,store,expected_release):
    if store.active_root is None:raise ApiError(503,'intake_disabled','当前服务未配置持久目录')
    with store._lock:
        release=store.get();mid=model_id(record['record_id'])
        # Recover an interrupted acknowledgement without publishing a second copy.
        with closing(sqlite3.connect(Path(release.repo.db_path).as_uri()+'?mode=ro',uri=True)) as con:
            existing=con.execute('SELECT first_source_record_id FROM phone_models WHERE model_id=?',(mid,)).fetchone()
        if existing:
            if existing[0]!=record_origin(record):raise ApiError(409,'intake_conflict','此编号已发布其他内容，请核对当前手机库，不能覆盖已发布机型')
            return deepcopy(release.key)
        if release.key!=expected_release:raise ApiError(409,'release_changed','手机库版本已更新，请刷新并重新核对发布内容')
        directory,checked=build_snapshot(record,store,release)
        pointer={'directory':directory,'snapshot_sha256':checked.key['snapshot_sha256'],'manifest_sha256':checked.key['manifest_sha256'],
                 'base_snapshot_sha256':file_sha(store.db_path)}
        fd,name=tempfile.mkstemp(dir=store.active_root,prefix='pointer-',suffix='.json')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump(pointer,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
            os.replace(name,store.active_root/'current.json')
            if os.name!='nt':
                directory_fd=os.open(store.active_root,os.O_RDONLY)
                try:os.fsync(directory_fd)
                finally:os.close(directory_fd)
        finally:
            if os.path.exists(name):os.unlink(name)
        return deepcopy(checked.key)
