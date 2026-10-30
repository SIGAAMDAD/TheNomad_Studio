#!/usr/bin/env python3
"""Create a reproducible, changed-files-only Studio contribution archive.

The selected source snapshot and the baseline snapshot are compared by bytes.
Every changed path must belong to the declared owner in the pinned roster.
The returned detached manifest is required for independent SHA-256 verification.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from studio.app.intake import Roster, digest, is_owned, normalized, MAX_FILE_SIZE, MAX_FILES, MAX_TOTAL_SIZE

EXCLUDED_DIRECTORIES = frozenset({'.git', '.pytest_cache', '.mypy_cache', '.ruff_cache', '__pycache__', '.venv', 'node_modules', 'dist', 'build'})
EXCLUDED_SUFFIXES = frozenset({'.pyc', '.pyo'})


def _snapshot(root: Path, paths: tuple[str, ...], *, allow_unowned: bool) -> dict[str, bytes]:
    """Return only owned source files; reject unowned changes in the contribution tree."""
    if not root.is_dir():
        raise ValueError(f'source directory does not exist: {root}')
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f'symlinks prohibited in contribution: {relative}')
        if not path.is_file() or path.suffix in EXCLUDED_SUFFIXES:
            continue
        name = normalized(relative.as_posix())
        if not is_owned(name, paths):
            if allow_unowned:
                continue
            raise ValueError(f'unowned source file in contribution: {name}')
        if path.stat().st_size > MAX_FILE_SIZE:
            raise ValueError(f'file exceeds handoff limit: {name}')
        result[name] = path.read_bytes()
    if len(result) > MAX_FILES or sum(map(len, result.values())) > MAX_TOTAL_SIZE:
        raise ValueError('contribution exceeds handoff limits')
    return result


def package(source: Path, packet: Path, output: Path, manifest: Path, *,
            owner: str, base_revision: str, baseline: Path | None = None,
            test_records: list[dict] | None = None) -> dict:
    roster = Roster.load(packet)
    if owner not in roster.paths:
        raise ValueError(f'unknown contribution owner: {owner}')
    if not isinstance(base_revision, str) or re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', base_revision) is None:
        raise ValueError('a verified Git commit SHA source baseline revision is required')
    paths = roster.paths[owner]
    current = _snapshot(source, paths, allow_unowned=False)
    prior = _snapshot(baseline, paths, allow_unowned=True) if baseline is not None else {}
    changed = {name for name in current.keys() | prior.keys() if prior.get(name) != current.get(name)}
    if not changed:
        raise ValueError('contribution has no changes')
    delta = {name: current[name] for name in changed if name in current}
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    # Fixed timestamp, permissions, sorted paths and compression level make exact bytes repeatable.
    with ZipFile(output, 'w', compression=ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for name, data in sorted(delta.items()):
            info = ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info._compresslevel = 9
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    metadata = {
        'group': owner,
        'plan_sha256': roster.plan_sha256,
        'base_revision_id': base_revision,
        'files': {name: {'before': digest(prior[name]) if name in prior else None,
                         'after': digest(current[name]) if name in current else None}
                  for name in sorted(changed)},
        'tests': test_records or [],
    }
    manifest.write_text(json.dumps(metadata, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    verified = roster.verify_handoff(output, metadata)
    if len(verified.changes) != len(changed):
        raise AssertionError('handoff verification did not cover all changes')
    source_bytes = sum(len(data) for data in delta.values())
    packed_bytes = sum(info.compress_size for info in ZipFile(output).infolist())
    return {'owner': owner, 'changed_paths': len(changed), 'archived_paths': len(delta),
            'uncompressed_bytes': source_bytes, 'compressed_bytes': packed_bytes,
            'compression_ratio': round(packed_bytes / source_bytes, 4) if source_bytes else 0,
            'zip_sha256': digest(output.read_bytes()), 'plan_sha256': roster.plan_sha256}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--owner', required=True, help='source owner from the pinned roster')
    parser.add_argument('--base-revision', required=True, help='verified source revision or commit SHA')
    parser.add_argument('--baseline-root', type=Path)
    parser.add_argument('--tests', type=Path)
    args = parser.parse_args()
    tests = json.loads(args.tests.read_text(encoding='utf-8')) if args.tests else []
    print(json.dumps(package(args.source_root, args.packet, args.output, args.manifest,
                             owner=args.owner, base_revision=args.base_revision,
                             baseline=args.baseline_root, test_records=tests), indent=2))


if __name__ == '__main__':
    main()
