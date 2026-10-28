"""Private transactional intake queue and reviewed catalog publishing."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .contracts import ApiError, RELEASE_FIELDS
from .intake_validation import normalize_payload
from catalog.publishing import publish_snapshot,model_id,record_origin

STATUSES={'draft','submitted','rejected','published'}
def now():return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
def invalid(message='录入请求格式无效'):raise ApiError(422,'invalid_intake',message)
def record_id(value):
    if not isinstance(value,str) or not re.fullmatch('I-[a-f0-9]{32}',value):invalid('录入编号无效')
    return value
def revision(value):
    if type(value) is not int or not 1<=value<=1000000:invalid('记录版本无效，请刷新队列')
    return value
def public_record(row):
    return {'record_id':row['record_id'],'status':row['status'],'revision':row['revision'],
            'created_at':row['created_at'],'updated_at':row['updated_at'],'payload':json.loads(row['payload']),
            'warnings':json.loads(row['warnings']), 'release':json.loads(row['release']) if row['release'] else None}


class IntakeStore:
    def __init__(self,path,release_store):self.path=Path(path);self.releases=release_store

    @contextmanager
    def connection(self):
        try:
            self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with closing(sqlite3.connect(self.path,timeout=30)) as con:
                con.row_factory=sqlite3.Row
                con.execute('''CREATE TABLE IF NOT EXISTS intake (
                    record_id TEXT PRIMARY KEY,status TEXT NOT NULL,revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload TEXT NOT NULL,
                    warnings TEXT NOT NULL,release TEXT)''')
                con.execute('CREATE INDEX IF NOT EXISTS intake_updated ON intake(updated_at)');con.commit()
                yield con
        except (sqlite3.Error,OSError,UnicodeError,ValueError) as exc:
            raise ApiError(503,'intake_unavailable','录入存储或发布暂不可用，请保留内容后重试') from exc

    def _row(self,con,item_id):
        row=con.execute('SELECT * FROM intake WHERE record_id=?',(record_id(item_id),)).fetchone()
        if row is None:raise ApiError(404,'intake_not_found','录入记录不存在，请刷新队列')
        if row['status']!='published':
            release=self.releases.get()
            with closing(sqlite3.connect(Path(release.repo.db_path).as_uri()+'?mode=ro',uri=True)) as catalog:
                origin=catalog.execute('SELECT first_source_record_id FROM phone_models WHERE model_id=?',(model_id(item_id),)).fetchone()
            if origin:
                if origin[0]!=record_origin(public_record(row)):raise ApiError(409,'intake_conflict','该编号已发布其他内容，请核对目录，不能覆盖已发布机型')
                con.execute("UPDATE intake SET status='published',revision=revision+1,updated_at=?,release=? WHERE record_id=?",(now(),json.dumps(release.key),item_id))
                row=con.execute('SELECT * FROM intake WHERE record_id=?',(item_id,)).fetchone()
        return row

    def _normalize(self,con,body,complete,editing=None):
        current=self.releases.get()
        with closing(sqlite3.connect(Path(current.repo.db_path).as_uri()+'?mode=ro',uri=True)) as catalog:
            existing=[{'brand':r[0],'display_name':r[1]} for r in catalog.execute('SELECT brand,display_name,first_source_record_id FROM phone_models')
                      if not (editing and str(r[2] or '').startswith('intake:'+editing+':'))]
        pending=[json.loads(r['payload']) for r in con.execute("SELECT record_id,payload FROM intake WHERE status IN ('draft','submitted')") if r['record_id']!=editing]
        normalized,errors,warnings=normalize_payload(body,existing,pending,complete=complete)
        if errors:invalid('；'.join(errors[:8]))
        return normalized,warnings

    def create(self,body,status):
        if status not in ('draft','submitted'):invalid()
        with self.connection() as con,con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute("SELECT count(*) FROM intake WHERE status IN ('draft','submitted')").fetchone()[0]>=1000:
                raise ApiError(429,'intake_queue_full','待处理记录较多，请先审核已有记录')
            normalized,warnings=self._normalize(con,body,status=='submitted')
            item_id='I-'+uuid4().hex;created=now()
            con.execute('INSERT INTO intake VALUES (?,?,1,?,?,?,?,NULL)',(item_id,status,created,created,json.dumps(normalized,ensure_ascii=False,allow_nan=False),json.dumps(warnings,ensure_ascii=False)))
            record=public_record(self._row(con,item_id))
            return {'ok':True,'record':record,'warnings':warnings}

    def get(self,item_id):
        with self.connection() as con,con:
            con.execute('BEGIN IMMEDIATE')
            return {'record':public_record(self._row(con,item_id))}

    def list(self,pairs):
        query={}
        for key,value in pairs:
            if key not in ('page','page_size') or key in query:invalid('队列查询参数无效')
            query[key]=value
        for key,default,maximum in [('page',1,1000000),('page_size',20,100)]:
            raw=query.get(key,str(default))
            if len(raw)>len(str(maximum)) or not raw.isascii() or not raw.isdigit() or not 1<=int(raw)<=maximum:invalid('队列页码或每页条数无效')
            query[key]=int(raw)
        with self.connection() as con,con:
            con.execute('BEGIN IMMEDIATE')
            total=con.execute('SELECT count(*) FROM intake').fetchone()[0]
            ids=con.execute('SELECT record_id FROM intake ORDER BY updated_at DESC,record_id DESC LIMIT ? OFFSET ?',(query['page_size'],(query['page']-1)*query['page_size'])).fetchall()
            return {'records':[public_record(self._row(con,row[0])) for row in ids],'total':total,**query}

    def update(self,item_id,body):
        if type(body) is not dict or set(body)!={'payload','status','expected_revision'} or body['status'] not in ('draft','submitted'):invalid()
        expected=revision(body['expected_revision'])
        with self.connection() as con,con:
            con.execute('BEGIN IMMEDIATE');old=self._row(con,item_id)
            if old['status']=='published' or old['revision']!=expected:raise ApiError(409,'intake_changed','记录已变更或发布，请刷新后再编辑')
            normalized,warnings=self._normalize(con,body['payload'],body['status']=='submitted',item_id)
            con.execute('UPDATE intake SET status=?,revision=revision+1,updated_at=?,payload=?,warnings=? WHERE record_id=?',
                (body['status'],now(),json.dumps(normalized,ensure_ascii=False,allow_nan=False),json.dumps(warnings,ensure_ascii=False),item_id))
            return {'ok':True,'record':public_record(self._row(con,item_id)),'warnings':warnings}

    def reject(self,item_id,body):
        if type(body) is not dict or set(body)!={'expected_revision'}:invalid()
        expected=revision(body['expected_revision'])
        with self.connection() as con,con:
            con.execute('BEGIN IMMEDIATE');old=self._row(con,item_id)
            if old['status']=='published' or old['revision']!=expected:raise ApiError(409,'intake_changed','记录已变更或发布，请刷新后重试')
            con.execute("UPDATE intake SET status='rejected',revision=revision+1,updated_at=? WHERE record_id=?",(now(),item_id))
            return {'ok':True,'record':public_record(self._row(con,item_id))}

    def publish(self,item_id,body):
        if type(body) is not dict or set(body)!={'expected_revision','expected_release','sources_verified'} or body['sources_verified'] is not True:invalid('发布前必须确认已核对来源')
        expected=revision(body['expected_revision']);release=body['expected_release']
        if type(release) is not dict or set(release)!=RELEASE_FIELDS or any(type(v) is not str or not 1<=len(v)<=256 for v in release.values()):invalid('手机库版本无效，请刷新')
        with self.connection() as con,con:
            con.execute('BEGIN IMMEDIATE');old=self._row(con,item_id)
            if old['status']=='published':return {'ok':True,'record':public_record(old),'release':json.loads(old['release'])}
            if old['status']!='submitted' or old['revision']!=expected:raise ApiError(409,'intake_changed','只可发布当前版本的待审核记录，请刷新队列')
            record=public_record(old)
            # Revalidate reviewed data before building; exclusion of this record allows editing its draft.
            self._normalize(con,record['payload'],True,item_id)
            published=publish_snapshot(record,self.releases,release)
            con.execute("UPDATE intake SET status='published',revision=revision+1,updated_at=?,release=? WHERE record_id=?",(now(),json.dumps(published),item_id))
            return {'ok':True,'record':public_record(self._row(con,item_id)),'release':published}

    def export(self):
        with self.connection() as con,con:
            con.execute('BEGIN IMMEDIATE');ids=con.execute('SELECT record_id FROM intake ORDER BY created_at,record_id').fetchall()
            return {'records':[public_record(self._row(con,row[0])) for row in ids]}
