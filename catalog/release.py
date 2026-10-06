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
    """Only validated resources are cached; both actual file hashes are checked per use."""
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()
        self._verified = None
        self._lock = RLock()

    def get(self) -> VerifiedRelease:
        with self._lock:
            try:
                db_sha = file_sha(self.db_path)
                mp_sha = file_sha(self.db_path.with_name('release_manifest.json'))
            except OSError as exc:
                self._verified = None
                raise ValueError('发布数据不可读取') from exc
            cached = self._verified
            if cached and (cached.key['snapshot_sha256'], cached.key['manifest_sha256']) == (db_sha, mp_sha):
                return cached
            self._verified = None
            verified = verify_release(self.db_path, mp_sha, db_sha)
            self._verified = verified
            return verified
