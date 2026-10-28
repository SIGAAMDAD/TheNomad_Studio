#!/usr/bin/env python3
"""Repeatable local SQLite query-plan and latency evidence; no provider attestation.

This is a single-process temporary-file benchmark. Results are observations, not
production latency/SLO approval or a substitute for off-host/load acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import tempfile
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.migrate import migrate

QUERIES = {
    'department_inbox_baseline_scan': (
        'SELECT sequence,event_id FROM department_events NOT INDEXED WHERE recipient_department=? '
        'AND recipient_id=? AND (sequence+0)>? ORDER BY sequence LIMIT 50',
        ('design', 'recipient_perf', None),
        None),
    'department_inbox_indexed': (
        'SELECT sequence,event_id FROM department_events WHERE recipient_department=? '
        'AND recipient_id=? AND sequence>? ORDER BY sequence LIMIT 50',
        ('design', 'recipient_perf', None),
        'department_recipient_sequence_idx'),
    'due_outbox': (
        "SELECT outbox_id,event_id FROM outbox WHERE status IN ('PREPARED','RETRY') "
        'AND next_retry_at<=? ORDER BY next_retry_at,created_at LIMIT 50',
        (2_000_000_000_000_000,),
        'due_outbox_idx'),
}


def _percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[min(len(ordered)-1, int((len(ordered)-1)*fraction))], 3)


def run_benchmark(*, rows=3000, iterations=250):
    if type(rows) is not int or not 100 <= rows <= 50000:
        raise ValueError('rows must be between 100 and 50000')
    if type(iterations) is not int or not 10 <= iterations <= 10000:
        raise ValueError('iterations must be between 10 and 10000')
    if sqlite3.sqlite_version_info < (3,45,0):
        raise RuntimeError('SQLite 3.45 or newer required')
    with tempfile.TemporaryDirectory(prefix='nomad-sqlite-perf-') as temp:
        path = Path(temp) / 'perf.sqlite'
        with ConnectionOwner(path) as owner:
            migrate(owner)
            with owner.write() as (db, now):
                for name in ('sender_perf', 'recipient_perf'):
                    db.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) "
                               "VALUES(?,'PERF',?,'WORKER','ACTIVE')", (name, name))
                for n in range(rows):
                    event_id = f'perf_event_{n:08d}'
                    db.execute('INSERT INTO event_journal(event_id,producer,transport_delivery_id,'
                               'partition_key,payload_sha256,canonical_event_json) '
                               "VALUES(?,'PERF',?,'perf',?,'{}')",
                               (event_id, event_id, 'a'*64))
                    db.execute('INSERT INTO department_events(event_id,sender_principal_id,'
                               'recipient_department,recipient_id,metadata_json,created_at) '
                               "VALUES(?,'sender_perf','design','recipient_perf','{}',?)", (event_id,now))
                    db.execute('INSERT INTO outbox(outbox_id,event_id,kind,destination,status,'
                               'next_retry_at,payload_sha256) VALUES(?,?,'
                               "'NOTICE','recipient_perf','PREPARED',?,?)",
                               (f'outbox_perf_{n:08d}', event_id, now, 'a'*64))
            report = dict(schema='NOMAD-SQLITE-PERF/1', kind='LOCAL_REPRODUCIBLE_OBSERVATION',
                          sqlite_version=sqlite3.sqlite_version, python=platform.python_version(),
                          platform=platform.platform(), rows=rows, iterations=iterations,
                          host_kind='single_local_process', external_provider='NOT_TESTED',
                          independent_qa='NOT_ATTESTED', queries={})
            with owner.connection(readonly=True) as db:
                inbox_results = {}
                for name, (sql, params, expected_index) in QUERIES.items():
                    if name.startswith('department_inbox'):
                        params = ('design', 'recipient_perf', rows-75)
                    plan = [r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql, params).fetchall()]
                    if expected_index and not any(expected_index in detail for detail in plan):
                        raise RuntimeError(f'{name}: required index absent from query plan: {plan}')
                    if not expected_index and not any('SCAN department_events' in detail for detail in plan):
                        raise RuntimeError(f'{name}: baseline full-history scan absent from query plan: {plan}')
                    for _ in range(25):
                        db.execute(sql, params).fetchall()
                    samples=[]
                    for _ in range(iterations):
                        started=time.perf_counter_ns()
                        rows_returned=db.execute(sql, params).fetchall()
                        samples.append((time.perf_counter_ns()-started)/1000)
                    if len(rows_returned) != 50:
                        raise RuntimeError(f'{name}: expected 50 rows, got {len(rows_returned)}')
                    if name.startswith('department_inbox'):
                        inbox_results[name] = rows_returned
                    report['queries'][name]=dict(sql=sql, params=list(params),
                                                  query_plan=plan, expected_index=expected_index,
                                                  rows_returned=len(rows_returned),
                                                  median_us=round(statistics.median(samples),3),
                                                  p95_us=_percentile(samples,.95),
                                                  p99_us=_percentile(samples,.99),
                                                  min_us=round(min(samples),3),
                                                  max_us=round(max(samples),3))
                if inbox_results['department_inbox_baseline_scan'] != inbox_results['department_inbox_indexed']:
                    raise RuntimeError('baseline and indexed inbox queries returned different content')
                baseline = report['queries']['department_inbox_baseline_scan']['median_us']
                indexed = report['queries']['department_inbox_indexed']['median_us']
                report['inbox_median_speedup_observed'] = round(baseline/indexed, 3)
            return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=3000)
    parser.add_argument('--iterations', type=int, default=250)
    parser.add_argument('--json', type=Path)
    args=parser.parse_args(argv)
    result=run_benchmark(rows=args.rows, iterations=args.iterations)
    output=json.dumps(result, indent=2, sort_keys=True)+'\n'
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(output)
    else:
        print(output,end='')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
