"""Online SQLite snapshot and non-destructive restore into a fresh private path."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import closing
from urllib.parse import quote

from studio_v16.db.sqlite.types import canonical, utc_us


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def readonly(path, *, immutable=False):
    return sqlite3.connect('file:' + quote(str(Path(path).resolve()), safe='/') +
                           '?mode=ro' + ('&immutable=1' if immutable else ''), uri=True)


def integrity(conn):
    if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('INTEGRITY_CHECK_FAILED')
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('FOREIGN_KEY_CHECK_FAILED')


def logical_manifest(conn):
    result = {}
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for name in names:
        info = conn.execute('PRAGMA table_info(' + identifier(name) + ')').fetchall()
        columns = [r[1] for r in info]
        keys = [r[1] for r in sorted(info, key=lambda c: c[5]) if r[5]]
        order = ','.join(map(identifier, keys or columns))
        digest, count, key_digest = hashlib.sha256(), 0, hashlib.sha256()
        for values in conn.execute('SELECT * FROM ' + identifier(name) + ' ORDER BY ' + order):
            value = dict(zip(columns, values))
            digest.update(canonical(value).encode() + b'\n')
            key_digest.update(canonical([value[k] for k in keys]).encode() + b'\n')
            count += 1
        result[name] = dict(rows=count, sha256=digest.hexdigest(), primary_keys=keys,
                            primary_key_sha256=key_digest.hexdigest(), columns=columns)
    sequences = dict(conn.execute('SELECT name,seq FROM sqlite_sequence'))
    ledger = conn.execute('SELECT version,checksum FROM schema_migrations ORDER BY version').fetchall()
    return dict(tables=result, sequences=sequences, migrations=ledger)


def private_destination(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('absolute non-symlink path required')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('private service-owned directory required')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    return path


def sync_file(path):
    with open(path, 'rb') as source:
        os.fsync(source.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(database, destination, *, timeout_seconds=120):
    if not 1 <= timeout_seconds <= 600:
        raise ValueError('bounded backup deadline required')
    destination = private_destination(destination)
    manifest_path = Path(str(destination) + '.manifest.json')
    if manifest_path.exists():
        destination.unlink()
        raise FileExistsError(manifest_path)
    start = time.monotonic()
    def progress(status, remaining, total):
        if time.monotonic() - start > timeout_seconds:
            raise TimeoutError('BACKUP_DEADLINE')
    try:
        with closing(readonly(database)) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target, pages=256, progress=progress, sleep=0.05)
            integrity(target)
            logical = logical_manifest(target)
        sync_file(destination)
        manifest = dict(schema='SQLITE-SNAPSHOT/1', created_at=utc_us(),
                        file_sha256=file_hash(destination), logical=logical,
                        backup_duration_seconds=time.monotonic() - start,
                        off_host_verified=False, rpo='UNMEASURED', rto='UNMEASURED')
        fd = os.open(manifest_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write(canonical(manifest) + '\n')
            output.flush()
            os.fsync(output.fileno())
        sync_file(manifest_path)
        destination.chmod(0o400)
        return manifest
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def verify_snapshot(database, manifest_path=None):
    manifest_path = manifest_path or str(database) + '.manifest.json'
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get('schema') != 'SQLITE-SNAPSHOT/1' or file_hash(database) != manifest['file_sha256']:
        raise ValueError('BACKUP_HASH_MISMATCH')
    with closing(readonly(database, immutable=True)) as conn:
        integrity(conn)
        actual = logical_manifest(conn)
    # Canonicalize tuples vs JSON arrays in the ledger.
    if canonical(actual) != canonical(manifest['logical']):
        raise ValueError('BACKUP_LOGICAL_MISMATCH')
    return manifest


def restore(snapshot_path, destination):
    manifest = verify_snapshot(snapshot_path)
    destination = private_destination(destination)
    start = time.monotonic()
    try:
        with closing(readonly(snapshot_path, immutable=True)) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            integrity(target)
            if canonical(logical_manifest(target)) != canonical(manifest['logical']):
                raise ValueError('RESTORE_LOGICAL_MISMATCH')
            # Restore never enables a second active writer. Original mode evidence
            # remains in the verified snapshot and its immutable manifest.
            target.execute("UPDATE authority_mode SET mode='SHADOW' WHERE singleton=1")
            target.commit()
            integrity(target)
        sync_file(destination)
        return dict(schema='SQLITE-RESTORE/1', restored_at=utc_us(), mode='SHADOW',
                    source_sha256=manifest['file_sha256'], logical_match_before_shadow=True,
                    restore_duration_seconds=time.monotonic() - start, activation='OWNER_GATED')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('snapshot', 'verify', 'restore'))
    parser.add_argument('database', type=Path)
    parser.add_argument('destination', nargs='?', type=Path)
    args = parser.parse_args()
    if args.operation != 'verify' and args.destination is None:
        parser.error('destination required')
    function = {'snapshot': snapshot, 'verify': verify_snapshot, 'restore': restore}[args.operation]
    print(canonical(function(args.database) if args.operation == 'verify' else function(args.database, args.destination)))


if __name__ == '__main__':
    main()
