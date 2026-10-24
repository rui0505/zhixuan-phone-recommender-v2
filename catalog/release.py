"""Framework-independent, hash-keyed verification of an immutable read-only release."""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
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
    """Only validated resources are cached; change detection uses stat, hashing on change.

    早先的实现每次 `get()` 都先对 2.9 MB 的库做一次 SHA256，**算完才判断缓存是否命中**，
    所以缓存只省了对象重建、没省任何 I/O；而 `_lock` 又让所有请求排队过这把锁。
    现在先用 (mtime_ns, size) 判断是否变更，只有真的变了才做全文件哈希——
    安全性不降：`verify_release` 内部仍然双次全文件哈希并校验清单，只是从"每请求"
    变成"每变更"。
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()
        self._verified = None
        self._stamp = None
        self._lock = RLock()

    def _stat_stamp(self):
        db = self.db_path.stat()
        manifest = self.db_path.with_name('release_manifest.json').stat()
        return (db.st_mtime_ns, db.st_size, manifest.st_mtime_ns, manifest.st_size)

    def get(self) -> VerifiedRelease:
        try:
            stamp = self._stat_stamp()
        except OSError as exc:
            self._verified = None
            self._stamp = None
            raise ValueError('发布数据不可读取') from exc
        cached = self._verified
        if cached is not None and self._stamp == stamp:
            return cached
        with self._lock:
            # 双重检查：等锁期间别的线程可能已经完成校验。
            if self._verified is not None and self._stamp == stamp:
                return self._verified
            self._verified = None
            self._stamp = None
            try:
                before = self._stat_stamp()
                db_sha = file_sha(self.db_path)
                mp_sha = file_sha(self.db_path.with_name('release_manifest.json'))
            except OSError as exc:
                raise ValueError('发布数据不可读取') from exc
            verified = verify_release(self.db_path, mp_sha, db_sha)
            after = self._stat_stamp()
            if after != before:
                # 校验期间文件被动过：不缓存，让下一次重新走完整校验。
                raise ValueError('发布数据在校验期间发生变化')
            self._verified = verified
            self._stamp = after
            return verified
