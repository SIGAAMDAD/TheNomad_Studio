"""Real SQLite authority and recovery tests; no sister implementation imported."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from nomad_studio.contracts.v1 import (CommandEnvelope, DomainEvent,
                                        QueryEnvelope, VerifiedIdentity)
from nomad_studio.state.api import build_component, open_store
from nomad_studio.state.errors import (Backpressure, ConfigurationError, IdempotencyConflict,
                                        InvalidState, OwnerLocked)
from nomad_studio.state.schema import SCHEMA_SHA256, _sql


def event(event_id: str, *, kind='RECORDED', payload=None, domain='shift') -> DomainEvent:
    return DomainEvent(event_id, domain, kind, 'worker:verified', 0, job_id='job-1',
                       assignment_epoch=3, payload=payload or {'status': 'ok'})



class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / 'authority.sqlite3'
        self.store = open_store({'path': str(self.path), 'max_pending': 1024})
        self.addCleanup(lambda: self.store.close())

    def test_actual_pragmas_and_integrity(self):
        settings = self.store.diagnostics()
        self.assertEqual(settings['journal_mode'], 'wal')
        self.assertEqual(settings['synchronous'], 2)
        self.assertEqual(settings['foreign_keys'], 1)
        self.assertEqual(settings['trusted_schema'], 0)
        self.assertTrue(self.store.integrity_check()['ok'])
        self.assertEqual(self.store.schema_identity(), {'version':1,'sha256':SCHEMA_SHA256})
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o077, 0)

    def test_second_writer_denied_even_in_same_process(self):
        with self.assertRaises(OwnerLocked):
            open_store({'path': str(self.path)})

    def test_wal_read_snapshot_and_no_second_write(self):
        self.store.transact_domain('shift', lambda tx: tx.put_record('shift', 'one', {'a': 1}))
        def reader(rd):
            row = rd.fetch_one('SELECT value_json FROM state_records WHERE namespace=?', ('shift',))
            self.assertEqual(json.loads(row['value_json']), {'a': 1})
            with self.assertRaises(sqlite3.OperationalError):
                rd._conn.execute("DELETE FROM state_records WHERE namespace='shift'")
        self.store.read(reader)

    def test_idempotent_effect_result_and_strict_fingerprint(self):
        calls = []
        def write(tx):
            calls.append(1)
            tx.append_event(event('evt-once'))
            return {'value': tx.put_record('shift', 'counter', {'n': 1})}
        first = self.store.transact_domain('shift', write, idempotency_key='same', fingerprint='a' * 64)
        second = self.store.transact_domain('shift', write, idempotency_key='same', fingerprint='a' * 64)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.diagnostics()['deduplicated'], 1)
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one('SELECT count(*) AS n FROM state_events'))['n'], 1)
        with self.assertRaises(IdempotencyConflict):
            self.store.transact_domain('shift', write, idempotency_key='same', fingerprint='b' * 64)

    def test_event_collision_and_redaction(self):
        self.store.transact(lambda tx: tx.append_event(event('unique')))
        self.store.transact(lambda tx: tx.append_event(event('unique')))
        with self.assertRaises(IdempotencyConflict):
            self.store.transact(lambda tx: tx.append_event(event('unique', payload={'changed': True})))
        with self.assertRaises(InvalidState):
            self.store.transact(lambda tx: tx.append_event(event('secret', payload={'token': 'redact-me'})))
        with self.assertRaises(InvalidState):
            self.store.transact(lambda tx: tx.append_event(event('bad-hash', payload={'ok': 1}).__class__(
                'bad-hash', 'shift', 'RECORDED', 'worker:verified', 0, payload_sha256='f' * 64,
                payload={'ok': 1})))
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one('SELECT count(*) n FROM state_events'))['n'], 1)

    def test_atomic_rollback_and_journal_gaps(self):
        def bad(tx):
            tx.put_record('shift', 'unfinished', {'x': 1})
            tx.append_event(event('not-committed'))
            raise RuntimeError('injected fault before COMMIT')
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            self.store.transact_domain('shift', bad, idempotency_key='retry-me')
        self.assertIsNone(self.store.read(lambda rd: rd.fetch_one(
            "SELECT * FROM state_records WHERE record_key='unfinished'")))
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one('SELECT count(*) n FROM state_events'))['n'], 0)
        self.store.transact_domain('shift', lambda tx: tx.append_event(event('not-committed')),
                                   idempotency_key='retry-me')
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one('SELECT count(*) n FROM state_events'))['n'], 1)

    def test_namespace_protection_and_version_cas(self):
        def records(tx):
            self.assertEqual(tx.put_record('shift', 'key', {'v': 1}, expected_version=0), 1)
            self.assertEqual(tx.get_record('shift', 'key')['version'], 1)
            self.assertEqual(tx.put_record('shift', 'key', {'v': 2}, expected_version=1), 2)
            with self.assertRaises(InvalidState):
                tx.put_record('review', 'key', {'v': 3})
            with self.assertRaises(InvalidState):
                tx.append_event(event('other-domain', domain='review'))
            with self.assertRaises(IdempotencyConflict):
                tx.delete_record('shift', 'key', expected_version=1)
        self.store.transact_domain('shift', records)
        with self.assertRaises(InvalidState):
            self.store.transact(lambda tx: tx.execute("DELETE FROM state_events WHERE event_id='x'"))
        with self.assertRaises(InvalidState):
            self.store.transact(lambda tx: tx.execute('PRAGMA foreign_keys=OFF'))

    def test_receipts_are_durable_and_conflict_checked(self):
        def save(tx):
            eid = tx.append_event(event('receipt-event'))
            return tx.record_receipt('shift', 'req-1', eid, {'state': 'PASS'})
        receipt = self.store.transact_domain('shift', save)
        self.assertEqual(self.store.transact_domain('shift', save), receipt)
        with self.assertRaises(IdempotencyConflict):
            self.store.transact_domain('shift', lambda tx: tx.record_receipt(
                'shift', 'req-1', 'receipt-event', {'state': 'REWORK'}))
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one('SELECT count(*) n FROM state_receipts'))['n'], 1)

    def test_read_rollback_snapshot_is_consistent(self):
        self.store.transact_domain('shift', lambda tx: tx.put_record('shift', 'a', 1))
        wait = threading.Event()
        done = threading.Event()
        observed = []
        def read_callback(rd):
            observed.append(rd.fetch_one('SELECT count(*) n FROM state_records')['n'])
            wait.set()
            self.assertTrue(done.wait(10))
            observed.append(rd.fetch_one('SELECT count(*) n FROM state_records')['n'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            f = pool.submit(self.store.read, read_callback)
            self.assertTrue(wait.wait(10))
            self.store.transact_domain('shift', lambda tx: tx.put_record('shift', 'b', 2))
            done.set()
            f.result(timeout=10)
        self.assertEqual(observed, [1, 1])

    def test_backup_restore_and_independent_integrity(self):
        self.store.transact_domain('shift', lambda tx: (tx.put_record('shift', 'archive-key', {'v': 7}),
                            tx.append_event(event('backup-event'))))
        dest = self.base / 'copy.sqlite3'
        proof = self.store.backup_snapshot(dest)
        restored = self.store.inspect_snapshot(dest)
        self.assertEqual(proof['sha256'], restored['sha256'])
        self.assertGreater(proof['bytes'], 0)
        with closing(sqlite3.connect(str(dest))) as conn:
            record = conn.execute("SELECT value_json FROM state_records WHERE record_key='archive-key'").fetchone()
            self.assertEqual(json.loads(record[0]), {'v': 7})
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        with self.assertRaises(ConfigurationError):
            self.store.backup_snapshot(dest)
        with open(dest, 'r+b') as f:
            f.truncate(200)
        with self.assertRaises((InvalidState, sqlite3.DatabaseError)):
            self.store.inspect_snapshot(dest)

    def test_non_local_filesystem_rejection_and_file_security(self):
        # Linux /dev/shm is tmpfs, which is not a durable authority disk.
        if os.path.exists('/dev/shm') and os.path.exists('/proc/self/mountinfo'):
            with self.assertRaises(ConfigurationError):
                with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
                    open_store({'path': d + '/authority.sqlite3'})
        fake = self.base / 'fake.sqlite3'
        fake.symlink_to(self.path)
        with self.assertRaises(ConfigurationError):
            open_store({'path': str(fake)})

    def test_500_concurrent_logical_clients(self):
        gate = threading.Barrier(500, timeout=20)
        def client(index):
            gate.wait()
            e = event('load-' + str(index), payload={'client': index})
            def do(tx):
                tx.append_event(e)
                tx.put_record('shift', 'load-' + str(index), {'value': index})
                return {'client': index}
            return self.store.transact_domain('shift', do, idempotency_key='load:' + str(index))
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=500) as pool:
            results = list(pool.map(client, range(500)))
        self.assertEqual([x['client'] for x in results], list(range(500)))
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one("SELECT count(*) n FROM state_events WHERE event_id LIKE 'load-%'"))['n'], 500)
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one("SELECT count(*) n FROM state_records WHERE namespace='shift'"))['n'], 500)
        sequences = self.store.read(lambda rd: rd.fetch_all(
            "SELECT sequence FROM state_events WHERE event_id LIKE 'load-%' ORDER BY sequence"))
        self.assertEqual(len(sequences), 500)
        self.assertTrue(all(a['sequence'] < b['sequence'] for a, b in zip(sequences, sequences[1:])))
        print('MEASURED 500 simultaneous client threads + durable events, wall seconds: %.3f' % (time.perf_counter() - started))

    def test_500_same_idempotency_key_race_executes_one_effect(self):
        barrier = threading.Barrier(500, timeout=20)
        calls = []
        def one(tx):
            calls.append(1)
            tx.append_event(event('500-dedup'))
            return 42
        def client(_):
            barrier.wait()
            return self.store.transact_domain('shift', one,
                       idempotency_key='500-race', fingerprint='a' * 64)
        with ThreadPoolExecutor(max_workers=500) as pool:
            values = list(pool.map(client, range(500)))
        self.assertEqual(values, [42] * 500)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.diagnostics()['deduplicated'], 499)
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one(
            "SELECT count(*) n FROM state_events WHERE event_id='500-dedup'"))['n'], 1)

    def test_raw_sqlite_busy_yields_bounded_backpressure(self):
        # A rogue external SQLite connection cannot bypass the operating lock
        # in production, but if it exists, the authority must fail boundedly.
        self.store.close()
        self.store = open_store({'path': str(self.path), 'busy_seconds': 0.05})
        with closing(sqlite3.connect(str(self.path), isolation_level=None)) as rogue:
            rogue.execute('BEGIN IMMEDIATE')
            with self.assertRaises(Backpressure):
                self.store.transact(lambda tx: tx.append_event(event('should-not-write')))
            rogue.rollback()
        self.assertIsNone(self.store.read(lambda rd: rd.fetch_one(
            "SELECT event_id FROM state_events WHERE event_id='should-not-write'")))

    def test_second_writer_process_denied(self):
        probe = '''
import sys
from nomad_studio.state.api import open_store
from nomad_studio.state.errors import OwnerLocked
try:
    s=open_store({'path': sys.argv[1]})
except OwnerLocked:
    sys.exit(0)
else:
    s.close()
    sys.exit(9)
'''
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2] / 'src') + os.pathsep + env.get('PYTHONPATH', '')
        proc = subprocess.run([sys.executable, '-c', probe, str(self.path)],
                              capture_output=True, text=True, timeout=10, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_bounded_writer_backpressure(self):
        self.store.close()
        tiny = open_store({'path': str(self.path), 'max_pending': 1, 'request_seconds': 3})
        self.store = tiny
        running, release = threading.Event(), threading.Event()
        def block(tx):
            running.set()
            self.assertTrue(release.wait(3))
            return 1
        with ThreadPoolExecutor(max_workers=3) as pool:
            active = pool.submit(tiny.transact, block)
            self.assertTrue(running.wait(2))
            queued = pool.submit(tiny.transact, lambda tx: 2)
            end = time.monotonic() + 2
            while tiny._queue.qsize() < 1 and time.monotonic() < end:
                time.sleep(0.001)
            with self.assertRaises(Backpressure):
                tiny.transact(lambda tx: 3)
            release.set()
            self.assertEqual(active.result(timeout=3), 1)
            self.assertEqual(queued.result(timeout=3), 2)

    def test_v1_initial_schema_is_complete_and_has_no_historical_migrations(self):
        identity = self.store.schema_identity()
        self.assertEqual(identity['version'], 1)
        self.assertEqual(identity['sha256'], hashlib.sha256(_sql().encode()).hexdigest())
        self.assertIsNone(self.store.read(lambda r:r.fetch_one(
            "SELECT name FROM sqlite_master WHERE name='state_migrations'")))
        self.assertIsNotNone(self.store.read(lambda r:r.fetch_one(
            "SELECT name FROM sqlite_master WHERE name='creative_policy_head'")))

    def test_fresh_v1_rejects_historical_schema_and_runtime_upgrade_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigurationError):
                open_store({'path':str(Path(directory)/'new.sqlite3'), 'migrations': []})
            with self.assertRaises(ConfigurationError):
                open_store({'path':str(Path(directory)/'other.sqlite3'), 'migration_roots': {}})
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / 'old.sqlite3'
            conn = sqlite3.connect(str(old))
            conn.execute('CREATE TABLE state_migrations(module TEXT)')
            conn.commit(); conn.close()
            with self.assertRaises(ConfigurationError):
                open_store({'path':str(old)})

    def test_denies_commented_ddl_and_cross_domain_table_writes(self):
        with self.assertRaises(InvalidState):
            self.store.transact_domain('shift', lambda tx: tx.execute('/* bypass */ DROP TABLE trust_launches;'))
        with self.assertRaises(InvalidState):
            self.store.transact_domain('shift', lambda tx: tx.execute('INSERT INTO trust_launches(ticket_id) VALUES(?)', ('fake',)))

    def test_read_row_limit(self):
        self.store.close()
        self.store = open_store({'path': str(self.path), 'max_read_rows': 2})
        for k in range(3):
            self.store.transact_domain('shift', lambda tx, k=k: tx.put_record('shift', str(k), k))
        with self.assertRaises(Backpressure):
            self.store.read(lambda rd: rd.fetch_all('SELECT * FROM state_records'))

    def test_private_permission_rejection(self):
        if os.name != 'posix':
            self.skipTest('POSIX permissions')
        self.store.close()
        self.path.chmod(0o644)
        with self.assertRaises(ConfigurationError):
            open_store({'path': str(self.path)})
        self.path.chmod(0o600)
        self.store = open_store({'path': str(self.path)})

    def test_restart_durable_idempotency(self):
        self.store.transact_domain('shift', lambda tx: (tx.append_event(event('restart-event')), 17)[1],
                                   idempotency_key='restart')
        self.store.close()
        self.store = open_store({'path': str(self.path)})
        called = []
        result = self.store.transact_domain('shift', lambda tx: called.append(1), idempotency_key='restart')
        self.assertEqual(result, 17)
        self.assertEqual(called, [])
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one('SELECT count(*) n FROM state_events'))['n'], 1)

    def test_transaction_rollback_survives_crash_without_schema_upgrade(self):
        self.store.transact_domain('shift', lambda tx: tx.put_record('shift','before', {'ok':True}))
        self.store.close()
        self.store=open_store({'path':str(self.path)})
        self.assertEqual(self.store.read(lambda rd:rd.fetch_one(
            "SELECT value_json FROM state_records WHERE namespace='shift' AND record_key='before'"))['value_json'], '{"ok":true}')


class ComponentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / 'authority.sqlite3'
        self.store = open_store({'path': str(self.path)})
        self.addCleanup(lambda: self.store.close())
        self.store.set_actor_epoch('owner', 4, issuer_principal_id='gateway')
        self.owner = VerifiedIdentity('owner', 'credible', 'trusted-issuer', 'local-tested',
                        frozenset({'state:read', 'state:audit', 'state:snapshot'}),
                        time.time_ns() // 1000 + 1_000_000_000, 4)
        self.snapshots = self.base / 'snapshots'; self.snapshots.mkdir(mode=0o700)
        self.component = build_component({'state_store': self.store, 'snapshot_dir': self.snapshots})

    def query(self, op, **kwargs):
        return self.component.query(QueryEnvelope('NOMAD/QUERY/1', 'query-1', 'state', op, **kwargs), self.owner)

    def command(self, key='snapshot-key', request='snapshot-req'):
        return CommandEnvelope('NOMAD/CMD/1', request, 'state', 'snapshot', key)

    def test_health_schema_events_and_pagination(self):
        self.assertEqual(self.query('health').payload['journal_mode'], 'wal')
        self.assertEqual(self.query('schema').payload['version'], 1)
        for i in range(3):
            self.store.transact(lambda tx, i=i: tx.append_event(event('page-' + str(i))))
        first = self.component.query(QueryEnvelope('NOMAD/QUERY/1', 'q1', 'state', 'events', limit=2, payload={'domain': 'shift'}), self.owner)
        self.assertEqual([row['event_id'] for row in first.payload['events']], ['page-0', 'page-1'])
        next_page = self.component.query(QueryEnvelope('NOMAD/QUERY/1', 'q2', 'state', 'events',
                                          limit=2, cursor=first.payload['next_cursor'], payload={'domain': 'shift'}), self.owner)
        self.assertEqual([row['event_id'] for row in next_page.payload['events']], ['page-2'])
        self.assertEqual(self.query('events', cursor='abc').code, 'INVALID_SCHEMA')
        self.assertEqual(self.query('events', payload={'secret': 'unexpected'}).code, 'INVALID_SCHEMA')

    def test_identity_scope_epoch_and_version_are_checked(self):
        self.assertEqual(self.component.execute(self.command(), replace(self.owner, current_authority_epoch=3)).code, 'STALE_ASSIGNMENT')
        self.assertEqual(self.component.execute(self.command(), replace(self.owner, scopes=frozenset())).code, 'INVALID_SCOPE')
        self.assertEqual(self.component.execute(self.command(), replace(self.owner, expires_at_us=1)).code, 'EXPIRED_GRANT')
        self.assertEqual(self.component.execute(replace(self.command(), version='NOMAD/CMD/2'), self.owner).code, 'MALFORMED_REQUEST')
        self.assertEqual(self.component.execute(replace(self.command(), payload={'path': '/etc/passwd'}), self.owner).code, 'MALFORMED_REQUEST')
        self.assertEqual(self.component.query(QueryEnvelope('NOMAD/QUERY/2', 'q', 'state', 'health'), self.owner).code, 'MALFORMED_REQUEST')
        self.store.set_actor_epoch('owner', 5, issuer_principal_id='gateway')
        self.assertEqual(self.component.execute(self.command(), self.owner).code, 'STALE_ASSIGNMENT')

    def test_snapshot_command_idempotency_and_receipt(self):
        self.store.transact(lambda tx: tx.append_event(event('snapshot-input')))
        done = self.component.execute(self.command(), self.owner)
        self.assertEqual(done.state, 'ACCEPTED', done)
        self.assertEqual(done.payload['integrity'], 'ok')
        self.assertEqual(len(done.payload['sha256']), 64)
        again = self.component.execute(self.command(), self.owner)
        self.assertEqual(again, done)
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one("SELECT count(*) n FROM state_events WHERE kind='SNAPSHOT_VERIFIED'"))['n'], 1)
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one("SELECT count(*) n FROM state_receipts WHERE namespace='state'"))['n'], 2)
        self.assertEqual(self.component.execute(self.command(key='another'), self.owner).code, 'IDEMPOTENCY_CONFLICT')

    def test_trusted_epoch_rotation_is_monotonic_and_journaled(self):
        self.assertEqual(self.store.actor_epoch('owner'), 4)
        self.store.set_actor_epoch('owner', 4, issuer_principal_id='gateway')
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one(
            "SELECT count(*) n FROM state_events WHERE kind='ACTOR_EPOCH_CHANGED'"))['n'], 1)
        with self.assertRaises(IdempotencyConflict):
            self.store.set_actor_epoch('owner', 3, issuer_principal_id='gateway')
        self.store.set_actor_epoch('owner', 5, issuer_principal_id='gateway')
        self.assertEqual(self.store.actor_epoch('owner'), 5)
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one(
            "SELECT count(*) n FROM state_events WHERE kind='ACTOR_EPOCH_CHANGED'"))['n'], 2)

    def test_atomic_epoch_fence_when_reassigned_during_admission(self):
        original = self.store.actor_epoch
        def rotating_epoch(principal):
            observed = original(principal)
            self.store.set_actor_epoch('owner', 5, issuer_principal_id='gateway')
            return observed
        self.store.actor_epoch = rotating_epoch
        denied = self.component.execute(self.command(request='racing'), self.owner)
        self.assertEqual(denied.state, 'DENIED')
        self.assertEqual(denied.code, 'STALE_ASSIGNMENT')
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one(
            "SELECT count(*) n FROM state_events WHERE kind='SNAPSHOT_REQUESTED'"))['n'], 0)

    def test_snapshot_tamper_after_verification_is_conflict(self):
        original = self.component.execute(self.command(request='tamper'), self.owner)
        self.assertEqual(original.state, 'ACCEPTED', original)
        path = self.snapshots / original.payload['snapshot_ref']
        with closing(sqlite3.connect(str(path))) as conn:
            conn.execute('CREATE TABLE tampered(data TEXT)')
            conn.commit()
        changed = self.component.execute(self.command(request='tamper'), self.owner)
        self.assertEqual(changed.state, 'CONFLICT')
        self.assertEqual(changed.code, 'DOCUMENT_CONFLICT')

    def test_corrupted_orphan_is_not_accepted(self):
        cmd = self.command(request='broken-orphan')
        fingerprint = hashlib.sha256(json.dumps({'principal': 'owner', 'request_id': 'broken-orphan'},
                                     sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        broken = self.snapshots / ('snapshot-' + fingerprint + '.sqlite3')
        broken.write_bytes(b'not a sqlite database')
        result = self.component.execute(cmd, self.owner)
        self.assertEqual(result.state, 'UNKNOWN')
        self.assertTrue(result.payload['pending'])
        self.assertEqual(self.store.read(lambda rd: rd.fetch_one(
            "SELECT count(*) n FROM state_events WHERE kind='SNAPSHOT_VERIFIED'"))['n'], 0)

    def test_snapshot_requires_configured_path_and_recoverable_orphan(self):
        unconfigured = build_component({'state_store': self.store})
        self.assertEqual(unconfigured.execute(self.command(), self.owner).code, 'NOT_CONFIGURED')
        # Inject crash window: file written, but completion not yet journaled.
        cmd = self.command(request='orphan')
        fingerprint = hashlib.sha256(json.dumps({'principal': 'owner', 'request_id': 'orphan'},
                                     sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        orphan = self.snapshots / ('snapshot-' + fingerprint + '.sqlite3')
        self.store.backup_snapshot(orphan)
        completed = self.component.execute(cmd, self.owner)
        self.assertEqual(completed.state, 'ACCEPTED', completed)
        self.assertEqual(completed.payload['snapshot_ref'], orphan.name)


if __name__ == '__main__':
    unittest.main()
