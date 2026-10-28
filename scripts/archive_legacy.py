#!/usr/bin/env python3
"""A02: sealed, offline, read-only Git source snapshots. Never activates authority.

A source snapshot is an evidence directory, not a live runtime data source.
All results are UNSIGNED until an independent archival authority seals them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

SHA = re.compile(r'^[0-9a-f]{40}$')


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE)


def hash_git_blob(value: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(value)).encode() + b'\0' + value).hexdigest()


def safe_label(ref: str) -> str:
    # No path composition, traversal or branch aliasing in archive filenames.
    if not re.fullmatch(r'[A-Za-z0-9_.\-/]{1,160}', ref) or any(
            p in ('', '.', '..') for p in ref.split('/')):
        raise ValueError('unsafe Git ref')
    return hashlib.sha256(ref.encode()).hexdigest()[:20]


def _entries(repo: Path, commit: str):
    for line in git(repo, 'ls-tree', '-r', '-z', commit).split(b'\0'):
        if not line:
            continue
        meta, raw = line.split(b'\t', 1)
        mode, kind, sha = meta.decode().split()
        path = raw.decode('utf-8', 'strict')
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise ValueError('unsupported Git object: ' + path)
        parts = Path(path).parts
        if not parts or any(part in ('', '.', '..') for part in parts) or Path(path).is_absolute():
            raise ValueError('unsafe Git path')
        yield path, sha


def snapshot(repo: Path, refs: list[str], destination: Path, *, expected: dict[str, str] | None = None) -> dict:
    repo, destination = repo.resolve(), destination.absolute()
    if not refs or len(set(refs)) != len(refs) or destination.exists():
        raise ValueError('nonempty unique refs and unused destination required')
    heads = {}
    for ref in refs:
        safe_label(ref)
        commit = git(repo, 'rev-parse', '--verify', ref + '^{commit}').decode().strip()
        if not SHA.fullmatch(commit) or (expected is not None and expected.get(ref) != commit):
            raise ValueError('unverified source ref: ' + ref)
        heads[ref] = commit
    destination.mkdir(parents=True, mode=0o700)
    snapshots = []
    try:
        for ref in refs:
            commit = heads[ref]
            label = safe_label(ref)
            tree = git(repo, 'rev-parse', commit + '^{tree}').decode().strip()
            records = []
            for path, sha in _entries(repo, commit):
                raw = git(repo, 'cat-file', 'blob', sha)
                if hash_git_blob(raw) != sha:
                    raise ValueError('Git blob mismatch for ' + path)
                output = destination / 'raw' / label / path
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open('xb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                records.append(dict(path=path, git_blob_sha=sha, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
            snapshots.append(dict(ref=ref, label=label, head=commit, tree=tree, count=len(records), records=records))
        manifest = dict(schema='NOMAD-A02-OFFLINE-GIT-ARCHIVE/1', authority='ARCHIVE_ONLY',
                        authorization='NOT_GRANTED', signature='UNSIGNED', snapshots=snapshots)
        serialized = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
        (destination / 'MANIFEST.json').write_bytes(serialized)
        (destination / 'MANIFEST.sha256').write_text(hashlib.sha256(serialized).hexdigest() + '\n')
        verify(destination)
        for node in sorted(destination.rglob('*'), reverse=True):
            node.chmod(0o500 if node.is_dir() else 0o400)
        destination.chmod(0o500)
        return manifest
    except BaseException:
        # An incomplete export is unsealed and must never be used for retirement.
        (destination / 'INCOMPLETE').write_text('Do not use for retirement or authority.\n')
        raise


def verify(destination: Path) -> dict:
    digest = (destination / 'MANIFEST.sha256').read_text().strip()
    raw = (destination / 'MANIFEST.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('archive manifest digest mismatch')
    manifest = json.loads(raw)
    if manifest.get('schema') != 'NOMAD-A02-OFFLINE-GIT-ARCHIVE/1' or manifest.get('authority') != 'ARCHIVE_ONLY':
        raise ValueError('unsupported archive')
    if (destination / 'INCOMPLETE').exists():
        raise ValueError('incomplete archive')
    expected_paths = {'MANIFEST.json', 'MANIFEST.sha256'}
    for item in manifest['snapshots']:
        label = safe_label(item['ref'])
        if label != item['label'] or not SHA.fullmatch(item['head']):
            raise ValueError('archive ref corrupted')
        if item['count'] != len(item['records']):
            raise ValueError('archive row count mismatch')
        for record in item['records']:
            rel = Path('raw') / label / record['path']
            if any(p in ('.', '..') for p in rel.parts) or rel.is_absolute():
                raise ValueError('unsafe archive record')
            expected_paths.add(rel.as_posix())
            file = destination / rel
            if file.is_symlink() or not file.is_file():
                raise ValueError('archive file missing or substituted')
            value = file.read_bytes()
            if (len(value) != record['bytes'] or hashlib.sha256(value).hexdigest() != record['sha256']
                    or hash_git_blob(value) != record['git_blob_sha']):
                raise ValueError('archive file integrity mismatch: ' + str(rel))
    actual = {x.relative_to(destination).as_posix() for x in destination.rglob('*') if x.is_file()}
    if actual != expected_paths:
        raise ValueError('missing or unexpected archive files')
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--ref', action='append', default=[])
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    result = verify(args.destination) if args.verify else snapshot(args.repo, args.ref, args.destination)
    print(json.dumps({'ref_count': len(result['snapshots']), 'authority': 'ARCHIVE_ONLY',
                      'signature': result['signature']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
