"""Exercise the production workforce transaction engine against real SQLite DDL.

The independent APSW runner is also built and tested in CI; this local fixture
uses sqlite3 only to validate SQL decisions when APSW is not installed.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from studio.storage.workforce import WorkforceStorage
from studio.workforce import WorkforceFault

DDL = Path(__file__).resolve().parents[2] / 'schema' / 'initial_schema.sql'


def connection(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def populated(path):
    conn = connection(path)
    conn.executescript(DDL.read_text())
    conn.execute("INSERT INTO projects(project_id,name,charter_digest,state) VALUES ('p','Project','digest','active')")
    return conn


def test_exact_result_replay_survives_fresh_connection(tmp_path):
    db = tmp_path / 'workforce.sqlite'
    conn = populated(db)
    store = WorkforceStorage(conn)
    sha = hashlib.sha256(b'payload').hexdigest()
    seen = []
    def command(tx):
        seen.append(1)
        assert tx.one("SELECT project_id FROM projects WHERE project_id=?", ('p',)) == {'project_id': 'p'}
        return {'state': 'registered', 'worker_id': 'one'}
    expected = store.command('p', 'workers.register', 'req', sha, command)
    store.close()
    store = WorkforceStorage(connection(db))
    replay = store.command('p', 'workers.register', 'req', sha,
                           lambda _tx: pytest.fail('idempotent command was rerun'))
    assert replay == expected
    assert len(seen) == 1
    row = store.read('p', lambda tx: tx.one('SELECT result_digest FROM idempotency WHERE project_id=?', ('p',)))
    assert row['result_digest'] == hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    store.close()


def test_conflicting_replay_is_rejected_without_running_command(tmp_path):
    store = WorkforceStorage(populated(tmp_path / 'conflict.sqlite'))
    sha = 'a' * 64
    store.command('p', 'workers.register', 'req', sha, lambda _tx: {'ok': True})
    with pytest.raises(WorkforceFault, match='different content'):
        store.command('p', 'workers.register', 'req', 'b' * 64, lambda _tx: pytest.fail('executed'))
    store.close()


def test_failure_rolls_back_durable_effect_and_idempotency(tmp_path):
    store = WorkforceStorage(populated(tmp_path / 'rollback.sqlite'))
    def fail(tx):
        tx.change("UPDATE projects SET name='Corrupted' WHERE project_id='p'")
        raise RuntimeError('interrupt before commit')
    with pytest.raises(RuntimeError):
        store.command('p', 'workers.register', 'req', 'a'*64, fail)
    assert store.read('p', lambda tx: tx.one("SELECT name FROM projects WHERE project_id='p'")) == {'name': 'Project'}
    assert store.read('p', lambda tx: tx.one("SELECT request_id FROM idempotency WHERE project_id='p'")) is None
    store.close()


def test_tampered_result_must_not_replay(tmp_path):
    conn = populated(tmp_path / 'tamper.sqlite')
    store = WorkforceStorage(conn)
    store.command('p', 'workers.register', 'req', 'a'*64, lambda _tx: {'ok': True})
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        conn.execute("UPDATE workforce_command_results SET result_json='{}' WHERE project_id='p'")
    # Simulate unauthorized schema tampering: runtime still refuses a replay.
    conn.execute('DROP TRIGGER workforce_result_cannot_update')
    conn.execute("UPDATE workforce_command_results SET result_json='{}' WHERE project_id='p'")
    with pytest.raises(WorkforceFault, match='unavailable or altered'):
        store.command('p', 'workers.register', 'req', 'a'*64, lambda _tx: pytest.fail('executed'))
    store.close()


def test_cross_project_and_bad_foreign_keys_do_not_write(tmp_path):
    store = WorkforceStorage(populated(tmp_path / 'scope.sqlite'))
    with pytest.raises(sqlite3.IntegrityError):
        store.command('foreign', 'workers.register', 'req', 'a'*64, lambda _tx: {'ok': True})
    with pytest.raises(WorkforceFault, match='project ID required'):
        store.read('', lambda _tx: [])
    store.close()


def test_same_owner_request_id_cannot_be_reused_for_another_command(tmp_path):
    store = WorkforceStorage(populated(tmp_path / 'owner-scope.sqlite'))
    try:
        expected=store.command('p','workers.register','shared','a'*64,
                               lambda _tx:{'worker_id':'one','state':'registered'})
        assert store.command('p','workers.register','shared','a'*64,
                             lambda _tx:pytest.fail('duplicate execution'))==expected
        with pytest.raises(WorkforceFault, match='different content'):
            store.command('p','workers.begin_shift','shared','a'*64,
                          lambda _tx:pytest.fail('cross-operation replay accepted'))
        owner=store.read('p',lambda tx:tx.one(
            'SELECT action_owner FROM idempotency WHERE project_id=? AND request_id=?',
            ('p','shared')))
        assert owner['action_owner']=='WG-I'
        assert store.command('p','D.submitFeature','shared','a'*64,
                             lambda _tx:{'feature_id':'new'})=={'feature_id':'new'}
        with pytest.raises(WorkforceFault, match='different content'):
            store.command('p','D.planFeature','shared','a'*64,
                          lambda _tx:pytest.fail('cross-operation replay accepted'))
        assert store.command('p','B.createCabal','shared','a'*64,
                             lambda _tx:{'cabal_id':'new'})=={'cabal_id':'new'}
    finally:
        store.close()
