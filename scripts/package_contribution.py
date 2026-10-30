#!/usr/bin/env python3
"""Reproducible, scoped source contribution with SHA-256 manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
from studio.app.intake import Roster, digest, is_owned
from studio.app.integration import tree_digest

EXCLUDED = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.git'}


def package(root: Path, packet: Path, output: Path, manifest: Path,
            test_records: list[dict] | None = None) -> dict:
    roster = Roster.load(packet)
    files = {}
    for file in root.rglob('*'):
        if file.is_symlink():
            raise ValueError('symlinks prohibited in contribution')
        if not file.is_file() or any(part in EXCLUDED for part in file.relative_to(root).parts):
            continue
        path = file.relative_to(root).as_posix()
        if path.endswith('.pyc') or '.egg-info/' in path:
            continue
        if not is_owned(path, roster.paths['M']):
            raise ValueError(f'unowned file in source tree: {path}')
        files[path] = file.read_bytes()
    if not files:
        raise ValueError('no changed source files')
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED, compresslevel=9) as zipfile:
        for name, data in sorted(files.items()):
            info = ZipInfo(name, (2026, 10, 30, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zipfile.writestr(info, data)
    metadata = {
        'group': 'M',
        'plan_sha256': roster.plan_sha256,
        'base_revision_id': tree_digest({}),
        'files': {name: {'before': None, 'after': digest(data)}
                  for name, data in sorted(files.items())},
        'tests': test_records or [],
    }
    manifest.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    verified = roster.verify_handoff(output, metadata)
    if len(verified.changes) != len(files):
        raise AssertionError('handoff verification did not cover all files')
    return {'files': len(files), 'zip_sha256': digest(output.read_bytes()),
            'plan_sha256': roster.plan_sha256}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--packet',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--tests',type=Path)
    args = parser.parse_args()
    test_records = json.loads(args.tests.read_text()) if args.tests else []
    print(json.dumps(package(args.source_root,args.packet,args.output,args.manifest,test_records),indent=2))


if __name__=='__main__':
    main()
