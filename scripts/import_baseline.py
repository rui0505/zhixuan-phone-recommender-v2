"""Import reviewed code only. Never execute archive contents or read secrets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import zipfile


def is_safe_member(name: str) -> bool:
    if not name or '\x00' in name or ':' in name:
        return False
    normalized = name.replace('\\', '/')
    posix, windows = PurePosixPath(normalized), PureWindowsPath(name)
    return not (posix.is_absolute() or windows.is_absolute() or windows.drive
                or '..' in posix.parts or normalized.startswith('/'))


def _allowed(name: str) -> bool:
    path = PurePosixPath(name.replace('\\', '/'))
    if any(part in {'.git', '__pycache__', 'node_modules', '.venv'} for part in path.parts):
        return False
    if path.name.startswith('.env') and path.name != '.env.example':
        return False
    if path.name == 'secrets.toml':
        return False
    return path.suffix.lower() in {'.py', '.md', '.json', '.example', '.html'} or name == 'data/phones.xlsx'


def import_reviewed_sources(zip_path: Path, pipeline_root: Path, destination: Path) -> dict:
    zip_path, pipeline_root, destination = map(Path, (zip_path, pipeline_root, destination))
    copied, skipped = [], []
    with zipfile.ZipFile(zip_path) as archive:
        entries = archive.infolist()
        for entry in entries:
            if not is_safe_member(entry.filename) or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('Unsafe ZIP member; no import performed')
        selected = [e for e in entries if not e.is_dir() and _allowed(e.filename)]
        targets = [(e, destination / e.filename.replace('\\', '/')) for e in selected]
        for _, target in targets:
            if not target.resolve().is_relative_to(destination.resolve()) or target.exists():
                raise ValueError('Import destination escapes root or already exists')
        for entry, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
            copied.append(target.relative_to(destination).as_posix())
        skipped.extend(e.filename for e in entries if not e.is_dir() and not _allowed(e.filename))
    for path in sorted(pipeline_root.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(pipeline_root)
        allowed = ((rel.parts[0] in {'src', 'tests'} and path.suffix in {'.py', '.mjs'})
                   or (len(rel.parts) == 1 and path.name in {'build_dataset.py', 'data_preview.py', 'id_registry.csv'}))
        if not allowed:
            continue
        target = destination / 'data_pipeline' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError('Pipeline destination already exists')
        shutil.copy2(path, target)
        copied.append(target.relative_to(destination).as_posix())
    for name in ('repository.py', 'decision_input.py'):
        path = pipeline_root / name
        if path.exists():
            target = destination / 'catalog' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError('Catalog destination already exists')
            shutil.copy2(path, target)
            copied.append(target.relative_to(destination).as_posix())
    return {'archive_sha256': hashlib.sha256(zip_path.read_bytes()).hexdigest(),
            'copied': copied, 'skipped': skipped}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', required=True, type=Path)
    parser.add_argument('--pipeline', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    report = import_reviewed_sources(args.zip, args.pipeline, args.destination)
    evidence = args.destination / 'local_evidence'
    evidence.mkdir(exist_ok=True)
    (evidence / 'import_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Imported {len(report['copied'])} reviewed files; skipped {len(report['skipped'])} entries")
