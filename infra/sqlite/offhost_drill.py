"""Operator-run restore rehearsal, producing unreviewed local/off-host evidence.

This tool never authorizes cutover or attests that a host is independent.
An operator must run it on an approved separate host and submit the captured
receipt and host/storage provenance to independent QA.
"""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
import socket
import sqlite3
import time
from contextlib import closing

from .backup import file_hash, readonly, restore, verify_snapshot
from studio_v16.db.sqlite.types import canonical, utc_us


def run_drill(snapshot_path, destination, *, claimed_source_host=None):
    """Verify a copied snapshot, restore SHADOW and report observed local facts."""
    snapshot_path = Path(snapshot_path)
    destination = Path(destination)
    started = time.monotonic()
    before = verify_snapshot(snapshot_path)
    manifest_sha256 = file_hash(Path(str(snapshot_path) + '.manifest.json'))
    restore_receipt = restore(snapshot_path, destination)
    with closing(readonly(destination)) as conn:
        restored_mode = conn.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone()[0]
        integrity_result = conn.execute('PRAGMA integrity_check').fetchall()
        fk_result = conn.execute('PRAGMA foreign_key_check').fetchall()
    if restored_mode != 'SHADOW' or integrity_result != [('ok',)] or fk_result:
        raise ValueError('RESTORED_DATABASE_NOT_SAFE')
    observed_host = socket.gethostname()
    if claimed_source_host is None:
        host_comparison = 'SOURCE_HOST_NOT_PROVIDED'
    elif claimed_source_host == observed_host:
        host_comparison = 'SAME_HOST_REPORTED'
    else:
        host_comparison = 'DIFFERENT_HOST_REPORTED_UNATTESTED'
    return {
        'schema': 'SQLITE-RESTORE-DRILL/1',
        'kind': 'OPERATOR_CAPTURE_UNREVIEWED',
        'captured_at': utc_us(),
        'source_host_claim': claimed_source_host,
        'restore_host_observed': observed_host,
        'host_comparison': host_comparison,
        'platform': platform.platform(),
        'sqlite_version': sqlite3.sqlite_version,
        'snapshot_sha256': before['file_sha256'],
        'manifest_sha256': manifest_sha256,
        'snapshot_created_at': before['created_at'],
        'snapshot_rows': {name: detail['rows'] for name, detail in before['logical']['tables'].items()},
        'restore_receipt': restore_receipt,
        'restore_mode': restored_mode,
        'foreign_key_violations': len(fk_result),
        'observed_local_drill_seconds': round(time.monotonic() - started, 6),
        'outage_rto_seconds': 'UNMEASURED',
        'rpo_seconds': 'UNMEASURED',
        'independent_qa': 'NOT_RUN',
        'off_host_verified': False,
        'cutover_authorized': False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path, help='copied backup file with adjacent .manifest.json')
    parser.add_argument('restore_path', type=Path, help='new path inside 0700 private directory')
    parser.add_argument('--source-host', help='source host name for comparison (self-reported, untrusted)')
    args = parser.parse_args(argv)
    print(canonical(run_drill(args.snapshot, args.restore_path,
                              claimed_source_host=args.source_host)))


if __name__ == '__main__':
    main()
