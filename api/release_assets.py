"""Resolve approved assets against the currently selected immutable snapshot."""
from pathlib import Path
from threading import RLock
from .images import ImageCatalog


class ReleaseImages:
    def __init__(self,store):self.store=store;self._cache=None;self._lock=RLock()
    def current(self,snapshot=None):
        with self._lock:
            if self._cache is not None and (snapshot is None or self._cache[0][1]==snapshot):return self._cache[1]
            release=self.store.get();root=Path(release.repo.db_path).parent
            if snapshot is not None and release.key['snapshot_sha256']!=snapshot:return None
            key=(str(root),release.key['snapshot_sha256'])
            if self._cache is None or self._cache[0]!=key:
                media=root/'media' if (root/'media').is_dir() else self.store.active_root/'media'
                self._cache=(key,ImageCatalog(root/'image_manifest.json',media,release.key['snapshot_sha256']))
            return self._cache[1]
    @property
    def count(self):return self.current().count
    def matches(self,snapshot):
        selected=self.current(snapshot)
        return selected is not None and selected.matches(snapshot)
    def for_model(self,model_id):return self.current().for_model(model_id)
    def read_for(self,filename,snapshot):
        selected=self.current(snapshot)
        return selected.read_for(filename,snapshot) if selected is not None else None
