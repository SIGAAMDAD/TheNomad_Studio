"""Verify all 13 immutable worker submissions against the one merged candidate.

WG-L's evidence ingestion format is described in qa/README.md.  It is NOT a
change to any frozen worker-to-platform contract.
"""
from __future__ import annotations

from pathlib import Path
import zipfile
import stat
from .core import digest, read_json, source_files, tree_digest, safe_relative
from .contract import WORKGROUPS, assert_owned, pinned_contract_digests, EXPECTED_PLAN_SHA

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_ZIP_BYTES = 300 * 1024 * 1024


class SubmissionError(ValueError):
    pass


def zip_contents(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    entries = {}
    running = 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = safe_relative(info.filename)
            if rel in entries:
                raise SubmissionError(f'duplicate entry: {rel}')
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise SubmissionError(f'symlink ZIP entry: {rel}')
            if info.file_size > MAX_FILE_BYTES:
                raise SubmissionError(f'oversized entry: {rel}')
            running += info.file_size
            if running > MAX_ZIP_BYTES:
                raise SubmissionError('archive uncompressed size limit exceeded')
            data = z.read(info)
            if len(data) != info.file_size:
                raise SubmissionError(f'bad ZIP entry size: {rel}')
            entries[rel] = digest(data)
    return entries


def verify_one(packet: Path, plan: dict, submissions: Path, worker: str) -> dict:
    archive = submissions / f'{worker}.zip'
    descriptor = submissions / f'{worker}.json'
    if not archive.exists() or not descriptor.exists():
        raise FileNotFoundError(f'{worker} ZIP and sidecar are both required')
    info = read_json(descriptor)
    if info.get('worker') != worker or info.get('baseline_plan_sha256') != EXPECTED_PLAN_SHA:
        raise SubmissionError(f'{worker}: wrong worker/plan baseline')
    if info.get('baseline_source_tree_sha256') != tree_digest({}):
        raise SubmissionError(f'{worker}: wrong greenfield empty-source baseline')
    files = zip_contents(archive)
    if not files:
        raise SubmissionError(f'{worker}: empty source archive')
    for rel in files:
        assert_owned(rel, worker, plan)
    if info.get('files') != files:
        raise SubmissionError(f'{worker}: manifest source bytes mismatch')
    if info.get('contracts') != pinned_contract_digests(packet):
        raise SubmissionError(f'{worker}: frozen contract digest mismatch')
    evidence = info.get('test_evidence', [])
    if not isinstance(evidence, list) or not evidence:
        raise SubmissionError(f'{worker}: no real command evidence submitted')
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get('command'), list) or not item['command']:
            raise SubmissionError(f'{worker}: missing exact argv test command')
        if not isinstance(item.get('exit_code'), int) or not isinstance(item.get('log_sha256'), str):
            raise SubmissionError(f'{worker}: missing exit code or output digest')
        log_file = item.get('log_file')
        if not isinstance(log_file, str):
            raise SubmissionError(f'{worker}: missing log file')
        # Sidecar logs are verifier artifacts, not candidate source files.
        log_path = submissions / safe_relative(log_file)
        if not log_path.is_file() or digest(log_path.read_bytes()) != item['log_sha256']:
            raise SubmissionError(f'{worker}: test log missing/tampered: {log_file}')
    return {'worker': worker, 'archive_sha256': digest(archive.read_bytes()),
            'manifest_sha256': digest(descriptor.read_bytes()), 'file_count': len(files), 'files': files}


def verify_all(packet: Path, plan: dict, submissions: Path, candidate: Path) -> dict:
    if not submissions.is_dir() or not candidate.is_dir():
        raise FileNotFoundError('merged source and all submissions required')
    observed = {}
    paths = {}
    missing = []
    for worker in WORKGROUPS:
        if not (submissions / f'{worker}.zip').is_file() or not (submissions / f'{worker}.json').is_file():
            missing.append(worker)
            continue
        info = verify_one(packet, plan, submissions, worker)
        for path, sha in info['files'].items():
            if path in paths:
                raise SubmissionError(f'overlapping ownership: {path} ({paths[path]} and {worker})')
            paths[path] = worker
        observed[worker] = info
    if missing:
        raise FileNotFoundError(f'missing source/sidecar from {missing}; all available submissions were checked')
    actual = source_files(candidate)
    unexpected = sorted(set(actual) - set(paths))
    if unexpected:
        raise SubmissionError(f'merged candidate has unverified extra source files: {unexpected[:25]}')
    missing_files = sorted(set(paths) - set(actual))
    if missing_files:
        raise SubmissionError(f'merged candidate lacks submitted source files: {missing_files[:25]}')
    for info in observed.values():
        for rel, sha in info['files'].items():
            if actual.get(rel) != sha:
                raise SubmissionError(f'merged bytes do not match worker submission: {rel}')
    # Source may include generated files; nothing unowned is falsely attributed to worker.
    return {'candidate_tree_sha256': tree_digest(actual),
            'candidate_files': len(actual), 'verified_groups': list(observed),
            'submissions': [{k: v for k, v in info.items() if k != 'files'} for info in observed.values()]}
