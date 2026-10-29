"""Moderated usage feedback, kept outside the immutable phone release."""
from contextlib import closing, contextmanager
import csv
import hashlib
import hmac
from io import StringIO
import os
from pathlib import Path
import secrets
import sqlite3
import time
import threading
from datetime import datetime, timezone
from uuid import UUID

from .contracts import ApiError

CATEGORIES = {'issue', 'suggestion', 'praise', 'other'}
PAGES = {'recommend', 'catalog', 'compare', 'data', 'other'}
STATUSES = {'pending', 'published', 'rejected'}
FIELDS = {'submission_id', 'nickname', 'rating', 'category', 'page', 'content'}
PUBLIC_COLUMNS = 'id,nickname,rating,category,page,content,created_at,published_at'


def invalid(message='反馈格式无效，请检查后再提交'):
    raise ApiError(422, 'invalid_feedback', message)


def identifier(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:invalid('反馈编号无效')
    except (ValueError, AttributeError):invalid('反馈编号无效')
    return value


def clean_text(value, minimum, maximum, label):
    if type(value) is not str:invalid(f'{label}须为文字')
    value = value.strip()
    if not minimum <= len(value) <= maximum:invalid(f'{label}须为 {minimum}–{maximum} 字')
    if any((ord(c) < 32 and c not in '\n\r\t') or 0xD800 <= ord(c) <= 0xDFFF for c in value):invalid(f'{label}含不可用字符')
    return value


def parse_feedback(body):
    if type(body) is not dict or set(body) != FIELDS:invalid()
    if type(body['rating']) is not int or body['rating'] not in range(1, 6):invalid('请选择 1–5 星体验评分')
    if type(body['category']) is not str or body['category'] not in CATEGORIES:invalid('请选择反馈类型')
    if type(body['page']) is not str or body['page'] not in PAGES:invalid('请选择涉及页面')
    return {'id':identifier(body['submission_id']),
            'nickname':clean_text(body['nickname'], 0, 20, '昵称') or '匿名用户',
            'rating':body['rating'], 'category':body['category'], 'page':body['page'],
            'content':clean_text(body['content'], 5, 2000, '反馈正文')}


def parse_query(pairs, admin=False):
    query = {}
    for key, value in pairs:
        if key in query or key not in ({'page', 'page_size', 'status'} if admin else {'page', 'page_size'}):invalid('反馈查询参数无效')
        query[key] = value
    for key, default, bound in (('page', 1, 1000000), ('page_size', 20, 100)):
        value = query.get(key, str(default))
        if len(value)>len(str(bound)) or not value.isascii() or not value.isdigit() or not 1 <= int(value) <= bound:invalid('反馈页码或每页条数无效')
        query[key] = int(value)
    query.setdefault('status', 'all')
    if query['status'] not in STATUSES | {'all'}:invalid('审核状态无效')
    return query


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


class Submission(dict):
    def __init__(self, row, created):
        super().__init__((key, row[key]) for key in ('id', 'status', 'created_at'))
        self.created = created


class FeedbackStore:
    def __init__(self, path=None, *, admin_token=None, clock=time.time):
        root = Path(__file__).resolve().parents[1]
        self.path = Path(path or os.environ.get('PHONE_FEEDBACK_DB') or root/'local_state'/'feedback.sqlite3')
        self.admin_token = admin_token if admin_token is not None else os.environ.get('PHONE_FEEDBACK_ADMIN_TOKEN')
        self.key_path = self.path.parent/'feedback-admin.key'
        self.clock = clock
        self._key_lock = threading.Lock()

    def _key(self):
        with self._key_lock:return self._load_key()

    def _load_key(self):
        if self.admin_token is not None:
            key = self.admin_token
        else:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:pass
            else:
                with os.fdopen(fd, 'w', encoding='ascii') as stream:stream.write(secrets.token_urlsafe(32)+'\n')
            key = self.key_path.read_text(encoding='ascii').strip()
        if len(key) < 32:raise OSError('invalid management key')
        return key

    @contextmanager
    def _connection(self):
        try:
            self._key()
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5)) as con:
                con.row_factory = sqlite3.Row
                con.executescript('''
                    CREATE TABLE IF NOT EXISTS feedback (
                        id TEXT PRIMARY KEY, nickname TEXT NOT NULL, rating INTEGER NOT NULL,
                        category TEXT NOT NULL, page TEXT NOT NULL, content TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL, published_at TEXT);
                    CREATE INDEX IF NOT EXISTS feedback_status_time ON feedback(status,created_at);
                    CREATE TABLE IF NOT EXISTS feedback_meta (name TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS feedback_limits (
                        source TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL,
                        PRIMARY KEY(source,bucket));
                ''')
                con.execute('INSERT OR IGNORE INTO feedback_meta VALUES (?,?)', ('rate_salt', secrets.token_hex(32)))
                con.commit()
                yield con
        except (sqlite3.Error, OSError, UnicodeError) as exc:
            raise ApiError(503, 'feedback_unavailable', '反馈存储暂不可用，请保留内容后重试') from exc

    def authorized(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer ') or len(authorization) > 1024:return False
        try:return hmac.compare_digest(authorization[7:].encode('utf-8'), self._key().encode('utf-8'))
        except (OSError, UnicodeError) as exc:
            raise ApiError(503, 'feedback_unavailable', '反馈管理暂不可用，请联系维护者') from exc

    def submit(self, body, source):
        value = parse_feedback(body)
        now = self.clock(); bucket = int(now // 3600)
        with self._connection() as con, con:
            con.execute('BEGIN IMMEDIATE')
            old = con.execute('SELECT * FROM feedback WHERE id=?', (value['id'],)).fetchone()
            if old:
                if any(old[k] != v for k, v in value.items()):raise ApiError(409, 'feedback_conflict', '此反馈编号已提交其他内容，请重新填写一条')
                return Submission(old, False)
            salt = con.execute("SELECT value FROM feedback_meta WHERE name='rate_salt'").fetchone()[0]
            source_hash = hmac.new(salt.encode(), str(source).encode(), hashlib.sha256).hexdigest()
            current = con.execute('SELECT count FROM feedback_limits WHERE source=? AND bucket=?', (source_hash, bucket)).fetchone()
            if current and current[0] >= 10:raise ApiError(429, 'feedback_rate_limit', '本小时提交较多，请稍后再试；已填写内容会保留')
            con.execute('DELETE FROM feedback_limits WHERE bucket < ?', (bucket-48,))
            con.execute('INSERT INTO feedback_limits VALUES (?,?,1) ON CONFLICT(source,bucket) DO UPDATE SET count=count+1', (source_hash, bucket))
            value.update(status='pending', created_at=timestamp(now), updated_at=timestamp(now), published_at=None)
            con.execute('INSERT INTO feedback ('+','.join(value)+') VALUES ('+','.join('?' for _ in value)+')', tuple(value.values()))
            return Submission(value, True)

    def _list(self, query, admin=False):
        params = parse_query(query, admin)
        status = params['status'] if admin else 'published'
        where = '' if status == 'all' else ' WHERE status=?'
        values = () if status == 'all' else (status,)
        with self._connection() as con, con:
            con.execute('BEGIN')
            total = con.execute('SELECT count(*) FROM feedback'+where, values).fetchone()[0]
            columns = '*' if admin else PUBLIC_COLUMNS
            order = 'created_at DESC,id DESC' if admin else 'published_at DESC,id DESC'
            items = [dict(row) for row in con.execute('SELECT '+columns+' FROM feedback'+where+' ORDER BY '+order+' LIMIT ? OFFSET ?', values+(params['page_size'], (params['page']-1)*params['page_size']))]
            result = dict(items=items, total=total, page=params['page'], page_size=params['page_size'])
            if admin:
                counts = {key:0 for key in STATUSES}
                counts.update({r[0]:r[1] for r in con.execute('SELECT status,count(*) FROM feedback GROUP BY status')})
                result['counts'] = counts
            else:
                average = con.execute("SELECT avg(rating) FROM feedback WHERE status='published'").fetchone()[0]
                result['average_rating'] = round(average, 1) if average is not None else None
            return result

    def public(self, query):return self._list(query)

    def list_admin(self, query):return self._list(query, True)

    def moderate(self, item_id, body):
        identifier(item_id)
        if type(body) is not dict or set(body) != {'status'} or type(body['status']) is not str or body['status'] not in STATUSES:invalid('审核状态须为待审核、已发布或未通过')
        status = body['status']; now = timestamp(self.clock())
        with self._connection() as con, con:
            con.execute('BEGIN IMMEDIATE')
            old = con.execute('SELECT status,published_at FROM feedback WHERE id=?', (item_id,)).fetchone()
            if old is None:raise ApiError(404, 'feedback_not_found', '反馈不存在，请刷新列表')
            published_at = (old['published_at'] or now) if status == 'published' else None
            con.execute('UPDATE feedback SET status=?,updated_at=?,published_at=? WHERE id=?', (status, now, published_at, item_id))
            return {'id':item_id, 'status':status, 'updated_at':now}

    def export_csv(self):
        def cell(value):
            value = '' if value is None else str(value)
            return "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) else value
        with self._connection() as con, con:
            con.execute('BEGIN')
            rows = con.execute('SELECT * FROM feedback ORDER BY created_at DESC,id DESC')
            output = StringIO(newline=''); writer = csv.writer(output)
            writer.writerow([column[0] for column in rows.description])
            for row in rows:writer.writerow([cell(value) for value in row])
        return ('\ufeff'+output.getvalue()).encode('utf-8')

    def backup(self, destination):
        destination = Path(destination)
        if not self.path.is_file():raise FileNotFoundError('Feedback database has not been created yet')
        if destination.resolve() == self.path.resolve():raise ValueError('Backup must use a different path')
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Refuse overwriting an earlier backup; sqlite backup takes a consistent snapshot.
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
