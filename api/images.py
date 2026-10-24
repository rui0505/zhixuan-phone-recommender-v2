"""Separately approved display assets; never change the numeric catalog snapshot."""
import hashlib
import json
from pathlib import Path
import re


class ImageCatalog:
    def __init__(self, manifest, media_root, snapshot_sha256):
        self.snapshot_sha256=snapshot_sha256
        self.root=Path(media_root).resolve(); self.models={}; self.files={}
        try:
            data=json.loads(Path(manifest).read_text(encoding='utf-8'))
            if data.get('approval')!='OWNER_APPROVED_PUBLIC_DISPLAY' or data.get('snapshot_sha256')!=snapshot_sha256:return
            for model_id,item in data['models'].items():
                filename=item['filename']; digest=item['sha256']
                if not re.fullmatch(r'[A-Za-z0-9_-]+\.webp',filename) or not re.fullmatch(r'[0-9a-f]{64}',digest):continue
                path=self.root/filename
                if path.is_symlink() or path.resolve().parent!=self.root or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:continue
                self.models[model_id]={'url':'/media/'+filename,'alt':item.get('alt','机型展示图'),
                                      'color':item.get('color'), 'scope':'model',
                                      'note':'机型展示图，不代表推荐配置的实际颜色、成色或库存'}
                self.files[filename]=digest
        except (OSError,ValueError,KeyError,TypeError):
            self.models={}; self.files={}

    @property
    def count(self):return len(self.models)

    def matches(self,snapshot_sha256):
        return bool(snapshot_sha256 and snapshot_sha256==self.snapshot_sha256)

    def read_for(self,filename,snapshot_sha256):
        if not self.matches(snapshot_sha256):return None
        digest=self.files.get(filename)
        if not digest:return None
        path=self.root/filename
        try:
            if path.is_symlink() or path.resolve().parent!=self.root:return None
            content=path.read_bytes()
            return content if hashlib.sha256(content).hexdigest()==digest else None
        except OSError:return None

    def for_model(self,model_id):
        # 哈希在 __init__ 里已逐文件校验过一次；建卡时再读一遍图并 SHA256
        # 只会让目录页为整库付出几十兆 I/O，校验强度没有任何提升。
        item=self.models.get(model_id)
        if item and item['url'].rsplit('/',1)[-1] in self.files:return dict(item)
        return None

    def path_for(self,filename):
        digest=self.files.get(filename)
        if not digest:return None
        path=self.root/filename
        try:
            if path.is_symlink() or path.resolve().parent!=self.root:return None
            return path if hashlib.sha256(path.read_bytes()).hexdigest()==digest else None
        except OSError:return None

