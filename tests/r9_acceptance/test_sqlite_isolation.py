"""Authoritative R8/R9 fresh-schema, ownership and cross-feature isolation probes."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'CONTRACTS' / 'initial_schema_v1.sql'
OWNERS = ROOT / 'CONTRACTS' / 'schema_table_owners.json'
D = 'a' * 64
E = 'b' * 64
F = 'c' * 64


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:', isolation_level=None)
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript(SCHEMA.read_text())
    assert c.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    yield c
    c.close()


def install_feature(c, project, feature, task):
    c.execute('INSERT INTO v1_projects VALUES(?,?,?,?,?)', (project, 'owner', D, 'ACTIVE', 1))
    c.execute('INSERT INTO v1_briefs VALUES(?,?,?,?,?,?,?,?)',
              (project, f'brief-{feature}', feature, 'owner', 'brief text', D, f'cmd-{feature}', 1))
    cdigest = F if feature == 'feature-a' else 'd' * 64
    c.execute('INSERT INTO v1_feature_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
              (project,feature,1,f'brief-{feature}',D,E,cdigest,D,D,D,3,'{}'))
    c.execute('INSERT INTO v1_feature_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
              (project,feature,f'brief-{feature}',cdigest,E,'PLANNED',1,1,None,1))
    c.execute('INSERT INTO v1_graph_revisions VALUES(?,?,?,?,?,?)',
              (feature,1,D,cdigest,E,'{}'))
    c.execute('INSERT INTO v1_tasks VALUES(?,?,?,?,?,?,?,?,?)',
              (feature,task,1,'cabal',D,'READY',None,1,'{}'))


def test_every_physical_table_has_one_authoritative_writer(conn):
    mapping = json.loads(OWNERS.read_text())['owner_per_table']
    actual = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'v1_%'")}
    assert actual == set(mapping)
    assert all(isinstance(v,str) and v for v in mapping.values())
    assert len(actual) == 56


def test_foreign_key_check_of_clean_schema(conn):
    assert list(conn.execute('PRAGMA foreign_key_check')) == []


def test_cross_project_dependency_and_artifact_refused(conn):
    install_feature(conn, 'project-a', 'feature-a', 'task-a')
    install_feature(conn, 'project-b', 'feature-b', 'task-b')
    # A task with a valid foreign key in another feature must be rejected.
    for wrong in [('feature-a','task-a','task-b'),('feature-b','task-a','task-b')]:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO v1_dependencies VALUES(?,?,?)',wrong)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('INSERT INTO v1_artifact_manifests VALUES(?,?,?,?,?,?,?,?)',
                     ('feature-a','artifact-x','task-b','CODE','src/x',D,'bytes:x','{}'))
    assert conn.execute('SELECT count(*) FROM v1_dependencies').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM v1_artifact_manifests').fetchone()[0] == 0


def test_same_feature_dependencies_and_artifact_admitted(conn):
    install_feature(conn, 'project-a', 'feature-a', 'task-a')
    conn.execute('INSERT INTO v1_tasks VALUES(?,?,?,?,?,?,?,?,?)',
                 ('feature-a','task-a2',1,'cabal',D,'READY',None,1,'{}'))
    conn.execute('INSERT INTO v1_dependencies VALUES(?,?,?)', ('feature-a','task-a','task-a2'))
    conn.execute('INSERT INTO v1_artifact_manifests VALUES(?,?,?,?,?,?,?,?)',
                 ('feature-a','artifact-ok','task-a2','CODE','src/x',D,'bytes:x','{}'))
    assert list(conn.execute('PRAGMA foreign_key_check')) == []


def test_revoked_or_nonexistent_task_refs_rejected(conn):
    install_feature(conn, 'project-a', 'feature-a', 'task-a')
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('INSERT INTO v1_dependencies VALUES(?,?,?)', ('feature-a','task-a','missing'))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('INSERT INTO v1_artifact_manifests VALUES(?,?,?,?,?,?,?,?)',
                     ('feature-a','artifact-x','missing','CODE','src/x',D,'bytes:x','{}'))


def test_schema_rollback_cannot_leave_partial_invalid_cross_feature_rows(conn):
    install_feature(conn, 'project-a', 'feature-a', 'task-a')
    install_feature(conn, 'project-b', 'feature-b', 'task-b')
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute('INSERT INTO v1_dependencies VALUES(?,?,?)', ('feature-a','task-a','task-b'))
    except sqlite3.IntegrityError:
        conn.rollback()
    else:
        conn.commit()
        pytest.fail('cross-feature row was admitted')
    assert conn.execute('SELECT COUNT(*) FROM v1_dependencies').fetchone()[0] == 0
    assert list(conn.execute('PRAGMA foreign_key_check')) == []
