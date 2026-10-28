#!/usr/bin/env python3
"""A00 complete tracked-tree import, workflow, caller and deletion-impact inventory.

Source reachability is evidence of code presence, NEVER evidence of deployment.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import posixpath
import re
import subprocess
from pathlib import Path

SOURCE_EXTENSIONS = {'.py', '.yml', '.yaml', '.json', '.md', '.sql', '.toml', '.ini', '.txt'}
DELETION_PREFIXES = ('studio/v1/', 'studio/architecture/hyperscale/v1/',
                     'studio/workforce/v4/', 'infra/db/', 'studio_v16/db/migrations/')
INDICATORS = {
    'postgres_runtime': r'PostgresStore|shadow_legacy|psycopg|POSTGRES_ACTIVE|POSTGRES_SHADOW',
    'git_runtime_writer': r'studio/runtime|studio/comms',
    'legacy_event_route': r'observe_legacy_runtime|observe_archived_events|work_event_router|W0[1-6]',
}
LINKS = re.compile(r'\]\(([^)#]+)(?:#[^)]+)?\)')
PUSH = re.compile(r'\bgit\s+(?:-\S+\s+)*push\b')
SCHEDULE = re.compile(r'^\s*schedule\s*:', re.M)
DISPATCH = re.compile(r'\brepository_dispatch\b|\bworkflow_dispatch\b')


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(('git', '-C', str(root), *args), stderr=subprocess.PIPE)


def tracked(root: Path):
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    if git(root, 'status', '--porcelain').strip():
        raise ValueError('working tree is dirty; exact committed snapshot required')
    paths = {}
    for row in git(root, 'ls-tree', '-r', '-z', 'HEAD').split(b'\0'):
        if not row:
            continue
        meta, raw_path = row.split(b'\t', 1)
        mode, kind, sha = meta.decode().split()
        if kind != 'blob':
            raise ValueError('unsupported tracked git object')
        paths[raw_path.decode()] = sha
    return head, paths


def imports(path, text):
    try:
        parsed = ast.parse(text, filename=path)
    except SyntaxError:
        return []
    result = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = Path(path).parent.parts
            base = parts[:len(parts) - node.level + 1] if node.level else ()
            name = '.'.join((*base, node.module)) if node.level else node.module
            if name:
                result.add(name)
    return sorted(result)


def resolve(name, paths):
    stem = name.replace('.', '/')
    return next((p for p in (stem + '.py', stem + '/__init__.py') if p in paths), None)


def classify(path):
    if path.startswith(DELETION_PREFIXES):
        return 'DELETION_CANDIDATE'
    if '/archive/' in path:
        return 'ARCHIVE_ONLY'
    if path.startswith(('studio_v16/', 'scripts/', '.github/workflows/')):
        return 'RUNNING'  # Source eligible, not proven active.
    return 'IMPORT_ONLY'


def build(root: Path, expected_head=None, expected_count=None):
    root = root.resolve()
    head, paths = tracked(root)
    if expected_head is not None and head != expected_head:
        raise ValueError('source HEAD mismatch: ' + head)
    if expected_count is not None and expected_count != len(paths):
        raise ValueError(f'tree count mismatch: {len(paths)} != {expected_count}')
    incoming = {p: [] for p in paths}
    graph, rows, workflows, broken = {}, [], [], []
    for path, sha in sorted(paths.items()):
        data = (root / path).read_bytes()
        real = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if real != sha:
            raise ValueError('source blob mismatch: ' + path)
        text = data.decode('utf-8', errors='replace') if Path(path).suffix in SOURCE_EXTENSIONS else ''
        marks = sorted(k for k, pat in INDICATORS.items() if re.search(pat, text))
        if path.endswith('.py'):
            found = sorted(set(filter(None, (resolve(i, paths) for i in imports(path, text)))))
            graph[path] = found
            for dep in found:
                incoming[dep].append(path)
        if path.startswith('.github/workflows/'):
            workflows.append(dict(path=path, schedule_declared=bool(SCHEDULE.search(text)),
                                  dispatch_declared=bool(DISPATCH.search(text)),
                                  git_push_referenced=bool(PUSH.search(text)),
                                  legacy_branch_referenced='git_runtime_writer' in marks))
        if path.endswith('.md'):
            for target in LINKS.findall(text):
                if target.startswith(('https:', 'http:', 'mailto:', 'data:', '#')):
                    continue
                candidate = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
                if candidate not in paths and not any(p.startswith(candidate.rstrip('/') + '/') for p in paths):
                    broken.append(dict(source=path, target=target))
        rows.append(dict(path=path, git_blob_sha=sha, bytes=len(data),
                         classification=classify(path), legacy_indicators=marks))
    impact = [dict(path=r['path'], python_importers=sorted(incoming[r['path']]),
                   requires_archive_receipt=True, deletion_authorized=False)
              for r in rows if r['classification'] == 'DELETION_CANDIDATE']
    output = dict(schema='NOMAD-A00-INVENTORY/1', source_head=head,
                  tracked_blob_count=len(paths), source_verified=True,
                  live_deployment_status='UNVERIFIED', pg_exists='UNKNOWN',
                  owner_deletion_authorization='NOT_PROVIDED', files=rows,
                  python_import_graph=graph, workflow_source_triggers=workflows,
                  deletion_impact=impact, relative_markdown_links_unresolved=broken)
    output['manifest_sha256'] = hashlib.sha256(
        json.dumps(output, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-head')
    parser.add_argument('--expected-count', type=int)
    a = parser.parse_args()
    report = build(a.root, a.expected_head, a.expected_count)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(f"A00 verified {report['tracked_blob_count']} blobs at {report['source_head']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
