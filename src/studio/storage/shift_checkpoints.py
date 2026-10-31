"""Crash-consistent C-owned checkpoint metadata with immutable verified bytes.

Installed as GitHubShiftTransport.save_checkpoint. Data is fsynced before C's
atomic checkpoint registration. An orphan file after a failed transaction has
no authority. Previously committed checkpoints survive process restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from studio.storage.workforce import WorkforceStorage


class CheckpointFault(RuntimeError):
    pass


def _id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is None:
        raise CheckpointFault('invalid checkpoint identity')
    return value


def _sha(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[a-f0-9]{40}', value) is None:
        raise CheckpointFault('checkpoint cursor must be a Git commit SHA')
    return value


def _canonical(doc: dict) -> bytes:
    return json.dumps(doc, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


class DurableShiftCheckpoints:
    """Checkpoint adapter; only verified active/stopping shifts may checkpoint."""

    def __init__(self, writer: WorkforceStorage, root: Path | str):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError('production checkpoint requires the C APSW writer')
        self.writer = writer
        self.root = Path(root).resolve()

    def _path(self, project: str, shift: str, checkpoint: str) -> Path:
        return self.root / _id(project) / _id(shift) / (_id(checkpoint) + '.json')

    def _decode(self, project: str, shift: str, checkpoint: str, digest: str) -> dict:
        path = self._path(project, shift, checkpoint)
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise CheckpointFault('checkpoint content missing') from exc
        if hashlib.sha256(body).hexdigest() != digest:
            raise CheckpointFault('checkpoint content no longer matches C digest')
        try:
            doc = json.loads(body)
        except (UnicodeError, ValueError, TypeError) as exc:
            raise CheckpointFault('checkpoint JSON is invalid') from exc
        if (not isinstance(doc, dict) or doc.get('project_id') != project
                or doc.get('shift_id') != shift or
                _canonical(doc) != body):
            raise CheckpointFault('checkpoint bytes or identity changed')
        return doc

    def save_checkpoint(self, *, project_id: str, worker_id: str,
                        shift_id: str, cursor: str | None,
                        processed: tuple[str, ...],
                        fingerprints: Mapping[str, str]) -> str:
        for key in (project_id, worker_id, shift_id):
            _id(key)
        if cursor is not None:
            _sha(cursor)
        if (not isinstance(processed, tuple) or len(processed) > 10000
                or not isinstance(fingerprints, Mapping) or len(fingerprints) > 10000):
            raise CheckpointFault('checkpoint entry limits exceeded')
        if len(set(processed)) != len(processed):
            raise CheckpointFault('duplicate processed checkpoint command')
        for command in processed:
            _id(command)
        hashes = {}
        for command, digest in fingerprints.items():
            _id(command)
            if not isinstance(digest, str) or re.fullmatch(r'[a-f0-9]{64}', digest) is None:
                raise CheckpointFault('invalid command fingerprint digest')
            hashes[command] = digest
        if not set(processed).issubset(hashes):
            raise CheckpointFault('processed command has no verified fingerprint')
        doc = {'project_id': project_id, 'worker_id': worker_id,
               'shift_id': shift_id, 'cursor': cursor,
               'processed': sorted(processed),
               'fingerprints': dict(sorted(hashes.items()))}
        body = _canonical(doc)
        if len(body) > 2_000_000:
            raise CheckpointFault('checkpoint exceeds bounded durable bytes')
        digest = hashlib.sha256(body).hexdigest()
        checkpoint = 'checkpoint_' + digest
        path = self._path(project_id, shift_id, checkpoint)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Immutable content first: C cannot commit a pointer to absent bytes.
        if path.exists():
            if path.read_bytes() != body:
                raise CheckpointFault('immutable checkpoint content changed')
        else:
            fd, temporary = tempfile.mkstemp(prefix='checkpoint-', suffix='.tmp', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                if os.name == 'posix':
                    directory = os.open(path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

        def commit(tx):
            current = tx.one(
                "SELECT state FROM worker_shifts WHERE project_id=? "
                "AND shift_id=? AND worker_id=?", (project_id, shift_id, worker_id))
            if current is None or current['state'] not in ('active', 'stopping'):
                raise CheckpointFault('checkpoint shift not verified active')
            previous = tx.one(
                "SELECT checkpoint_id,context_digest FROM checkpoints "
                "WHERE project_id=? AND shift_id=? ORDER BY rowid DESC LIMIT 1",
                (project_id, shift_id))
            if previous is not None:
                prior = self._decode(project_id, shift_id,
                                     previous['checkpoint_id'], previous['context_digest'])
                if prior['worker_id'] != worker_id:
                    raise CheckpointFault('checkpoint worker identity changed')
                if (not set(prior['processed']).issubset(processed)
                        or any(hashes.get(k) != v for k, v in prior['fingerprints'].items())
                        or prior['cursor'] is not None and cursor is None):
                    raise CheckpointFault('checkpoint would roll back verified work')
            tx.change("INSERT INTO checkpoints(project_id,checkpoint_id,shift_id,"
                      "context_digest,relay_cursor,recorded_at) VALUES(?,?,?,?,?,?)",
                      (project_id, checkpoint, shift_id, digest, cursor,
                       datetime.now(timezone.utc).isoformat(timespec='microseconds')))
            return {'checkpoint_id': checkpoint}

        result = self.writer.command(project_id, 'workers.checkpoint', checkpoint,
                                     digest, commit)
        if result.get('checkpoint_id') != checkpoint:
            raise CheckpointFault('checkpoint transaction returned wrong identity')
        return checkpoint

    def load_latest(self, *, project_id: str, worker_id: str, shift_id: str) -> dict | None:
        for key in (project_id, worker_id, shift_id):
            _id(key)
        def read(tx):
            shift = tx.one("SELECT worker_id FROM worker_shifts "
                           "WHERE project_id=? AND shift_id=?", (project_id, shift_id))
            if shift is None or shift['worker_id'] != worker_id:
                raise CheckpointFault('checkpoint owner mismatch')
            return tx.one("SELECT checkpoint_id,context_digest,relay_cursor "
                          "FROM checkpoints WHERE project_id=? AND shift_id=? "
                          "ORDER BY rowid DESC LIMIT 1", (project_id, shift_id))
        row = self.writer.read(project_id, read)
        if row is None:
            return None
        doc = self._decode(project_id, shift_id,
                           row['checkpoint_id'], row['context_digest'])
        if doc['worker_id'] != worker_id or doc['cursor'] != row['relay_cursor']:
            raise CheckpointFault('checkpoint does not match authoritative C state')
        return doc
