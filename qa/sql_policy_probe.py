"""Executable frozen SQL negative/positive cases, built on stock SQLite reference."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from .core import digest, read_json


def run(schema_path: Path, cases_path: Path) -> dict:
    schema_bytes = schema_path.read_bytes()
    case_bytes = cases_path.read_bytes()
    cases = read_json(cases_path).get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('no frozen reference cases')
    outputs = []
    seen = set()
    for case in cases:
        cid = case['id']
        if cid in seen:
            raise AssertionError(f'duplicate reference case: {cid}')
        seen.add(cid)
        expected = case['expect']
        if expected not in ('ACCEPT','REJECT'):
            raise ValueError(f'{cid}: unsupported reference expectation')
        db = sqlite3.connect(':memory:')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.executescript(schema_bytes.decode('utf-8'))
            if case.get('setup'):
                db.executescript(case['setup'])
            try:
                db.executescript(case['attack'])
                actual = 'ACCEPT'
            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                actual = 'REJECT'
            passed = actual == expected
            observed = None
            if passed and actual == 'ACCEPT' and 'assert_sql' in case:
                query = case['assert_sql'].strip()
                if not query.upper().startswith('SELECT '):
                    raise ValueError(f'{cid}: assert_sql must be read-only SELECT')
                row = db.execute(query).fetchone()
                observed = row[0] if row else None
                passed = observed == case.get('assert_value')
            outputs.append({'id':cid, 'expected':expected, 'actual':actual,
                            'assertion_value':observed, 'pass':passed})
        finally:
            db.close()
    return {'passed':all(c['pass'] for c in outputs), 'cases':outputs,
            'schema_sha256':digest(schema_bytes), 'cases_sha256':digest(case_bytes),
            'sqlite_version':sqlite3.sqlite_version}
