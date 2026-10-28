"""Framework-independent, hash-keyed verification of an immutable read-only release."""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import re
from threading import RLock

from .repository import CatalogRepository


def file_sha(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class VerifiedRelease:
    repo: CatalogRepository
    manifest: dict
    key: dict[str, str]


def verify_release(db_path: Path, manifest_sha: str, snapshot_sha: str) -> VerifiedRelease:
    path = Path(db_path).resolve()
    mp = path.with_name('release_manifest.json')
    try:
        if file_sha(path) != snapshot_sha or file_sha(mp) != manifest_sha:
            raise ValueError('发布文件已变化')
        manifest = json.loads(mp.read_text(encoding='utf-8'))
        if (manifest['status'] != 'PASS' or manifest['acceptance']['machine_acceptance'] != 'PASS'
                or manifest['snapshot_sha256'] != snapshot_sha):
            raise ValueError('发布数据机器验收或哈希不一致')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or con.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('数据库完整性校验失败')
        repo = CatalogRepository(str(path))
        version = repo.dataset_version()
        if (version.get('dataset_version') != manifest['dataset_version']
                or version.get('rule_version') != manifest['feature_rule_version']):
            raise ValueError('数据版本与发布清单不一致')
        if file_sha(path) != snapshot_sha or file_sha(mp) != manifest_sha:
            raise ValueError('校验期间发布文件已变化')
        key = dict(dataset_version=manifest['dataset_version'],
                   feature_rule_version=manifest['feature_rule_version'],
                   snapshot_sha256=snapshot_sha, manifest_sha256=manifest_sha)
        return VerifiedRelease(repo, manifest, key)
    except (OSError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('发布数据校验失败') from exc


class ReleaseStore:
    """Only validated resources are cached; both actual file hashes are checked per use."""
    def __init__(self, db_path: Path, active_root: Path | None = None):
        self.db_path = Path(db_path).resolve()
        self.active_root = Path(active_root).resolve() if active_root is not None else None
        self._verified = None
        self._lock = RLock()

    def selected_path(self):
        if self.active_root is None or not (self.active_root/'current.json').exists():return self.db_path,None
        try:
            pointer=json.loads((self.active_root/'current.json').read_text(encoding='utf-8'))
            if set(pointer)!={'directory','snapshot_sha256','manifest_sha256','base_snapshot_sha256'}:raise ValueError('活动发布指针格式无效')
            name=pointer['directory']
            if not isinstance(name,str) or not re.fullmatch(r'r-[a-f0-9]{32}',name):raise ValueError('活动发布路径无效')
            if any(not isinstance(pointer[k],str) or not re.fullmatch('[a-f0-9]{64}',pointer[k]) for k in pointer if k!='directory'):raise ValueError('活动发布指纹无效')
            path=self.active_root/'snapshots'/name/'catalog.sqlite'
            if (self.active_root/'snapshots').is_symlink() or path.is_symlink() or path.parent.is_symlink() or path.resolve().parent.parent!=(self.active_root/'snapshots').resolve():raise ValueError('活动发布路径越界')
            if file_sha(self.db_path)!=pointer['base_snapshot_sha256']:raise ValueError('基础目录版本变化，请先迁移管理员发布数据')
            return path,pointer
        except (OSError,KeyError,TypeError,json.JSONDecodeError) as exc:raise ValueError('活动发布指针不可读取') from exc

    def data_root(self):
        with self._lock:return self.selected_path()[0].parent

    def get(self) -> VerifiedRelease:
        with self._lock:
            try:
                path,pointer=self.selected_path()
                db_sha = file_sha(path)
                mp_sha = file_sha(path.with_name('release_manifest.json'))
                if pointer and (db_sha!=pointer['snapshot_sha256'] or mp_sha!=pointer['manifest_sha256']):raise ValueError('活动发布指纹变化')
            except OSError as exc:
                self._verified = None
                raise ValueError('发布数据不可读取') from exc
            cached = self._verified
            if cached and cached.repo.db_path==str(path) and (cached.key['snapshot_sha256'], cached.key['manifest_sha256']) == (db_sha, mp_sha):
                return cached
            self._verified = None
            verified = verify_release(path, mp_sha, db_sha)
            self._verified = verified
            return verified
