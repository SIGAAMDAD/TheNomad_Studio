"""Integrated R9 production root and persistent authority acceptance.

These tests execute the installed service classes and real SQLite schema, not
mock V1 facades. Native provider/host and independent QA are intentionally
NOT_VERIFIED by this local suite.
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from nomad_studio.app import PORT_NAMES, open_studio, ProviderUnavailable
from nomad_studio.contracts import runtime_ports_v1 as contracts
from nomad_studio.contracts.runtime_ports_v1 import Result
from nomad_studio.state.r9_authority import (
    AuthorityError, DurableOutbox, IdempotencyConflict, OwnerLocked,
    R9Authority, WriterDenied,
)

SHA = 'a' * 64
CLOCK = lambda: 1720000000000000


@pytest.fixture
def runtime(tmp_path):
    with open_studio(sqlite_path=tmp_path/'authority'/'studio.sqlite3',
                     cas_path=tmp_path/'cas', clock_us=CLOCK) as root:
        yield root


def test_all_22_frozen_ports_are_callable_with_exact_parameter_signatures(runtime):
    assert len(runtime.services) == 22
    assert tuple(runtime.services) == PORT_NAMES
    for port_name in PORT_NAMES:
        proto = getattr(contracts, port_name)
        service = runtime.port(port_name)
        for method_name, method in inspect.getmembers(proto, predicate=inspect.isfunction):
            if method_name.startswith('_'):
                continue
            candidate = getattr(service, method_name, None)
            assert callable(candidate), f'{port_name}.{method_name} missing'
            expected = inspect.signature(method)
            signature = inspect.signature(candidate)
            expected_fields = list(expected.parameters.values())[1:]
            actual_fields = list(signature.parameters.values())
            assert [(x.name, x.kind) for x in expected_fields] == [
                (x.name, x.kind) for x in actual_fields
            ], f'{port_name}.{method_name}: {expected} != {signature}'


def test_new_only_56_table_authority_and_no_legacy_install(runtime):
    tables = runtime.state.read(lambda tx: {
        x['name'] for x in tx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()})
    assert tables == set(runtime.state.table_owners)
    assert len(tables) == 56
    assert 'state_migrations' not in tables
    assert not hasattr(runtime, 'components')
    assert not hasattr(runtime, 'extensions')
    assert not hasattr(runtime, 'store')
    assert runtime.health()['database'] == 'R9_FRESH_SCHEMA_VERIFIED'


def test_project_and_brief_use_real_cross_service_persistent_data(runtime):
    owner = Result('OK', 'OK', {'principal_id':'owner-A'}, 'owner-fixture')
    spec = {'project_id':'game-A', 'owner_principal_id':'owner-A',
            'constitution_digest':SHA, 'initial_state':'ACTIVE'}
    project = runtime.port('ProjectPort')
    brief = runtime.port('BriefPort')
    assert project.register_project(spec, owner).status == 'OK'
    assert project.register_project(spec, owner).status == 'OK'
    assert project.get_project('game-A').record['state'] == 'ACTIVE'
    admission = brief.admit_brief({'brief_id':'brief-A', 'project_id':'game-A',
                                   'feature_id':'feature-A','original_text':'Implement quest',
                                   'owner_command_ref':'owner-fixture'}, owner)
    assert admission.status == 'OK', (admission.status, admission.code)
    assert brief.get_original_brief('brief-A', 'game-A').status == 'OK'
    original = admission.record
    assert original['original_digest'] == brief.get_original_brief('brief-A', 'game-A').record['original_digest']

    # Freeze the CAB-B contract using the *actual* frozen SQL columns. This
    # fixture bypasses the Bible/owner-provenance workflow only inside a test.
    with runtime.state.capability('CAB-B').transaction() as tx:
        tx.execute('INSERT INTO v1_feature_contracts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('game-A','feature-A',1,'brief-A',original['original_digest'],
                    'b'*64, 'c'*64, 'd'*64, 'e'*64, 'f'*64, 10, '{}'))
    contract = {'project_id':'game-A','feature_id':'feature-A','brief_id':'brief-A',
                'original_brief_digest':original['original_digest'],
                'bible_pair_digest':'b'*64, 'contract_digest':'c'*64}
    opened = runtime.port('DirectorPort').open_run(original, contract)
    assert opened.status == 'OK', (opened.status,opened.code)
    assert opened.record['status'] == 'RECEIVED'
    assert runtime.port('DirectorPort').open_run(original, contract).status == 'OK'
    # One atomic root event/outbox, not two, despite duplicate open_run.
    assert len(runtime.state.outbox_rows('PENDING')) == 1
    events = runtime.state.read(lambda tx: tx.fetch_all('SELECT * FROM v1_events'))
    assert len(events) == 1 and events[0]['feature_id'] == 'feature-A'


def test_closed_runtime_and_second_writer_lock(tmp_path):
    path = tmp_path/'authority'/'db.sqlite'
    with open_studio(sqlite_path=path) as root:
        assert root.port('ProjectPort')
        with pytest.raises(OwnerLocked):
            open_studio(sqlite_path=path)
    with open_studio(sqlite_path=path) as reopened:
        assert reopened.port('ProjectPort')
        assert reopened.health()['readiness'] == 'NOT_READY'
    with pytest.raises(RuntimeError):
        reopened.port('ProjectPort')


def test_writer_capabilities_reject_cross_cab_sql_and_ddl(runtime):
    cap = runtime.state.capability('CAB-B')
    for sql in ('DELETE FROM v1_bible_editions',
                'UPDATE v1_outbox SET state=\'ACKED\'',
                'CREATE TABLE injected(x TEXT)',
                'PRAGMA foreign_keys=OFF',
                "ATTACH DATABASE ':memory:' AS other"):
        with pytest.raises((WriterDenied, sqlite3.DatabaseError)):
            cap.write(lambda tx: tx.execute(sql))
    # PRAGMA is intentionally restricted to the authority, not the service.
    with pytest.raises((WriterDenied, sqlite3.DatabaseError)):
        cap.read('PRAGMA journal_mode')
    with pytest.raises(WriterDenied):
        runtime.state.capability('P2')
    with pytest.raises(WriterDenied):
        runtime.state.capability('CAB-Z')


def test_leaked_view_and_cursor_cannot_outlive_ownership(runtime):
    saved = {}
    runtime.state.capability('CAB-B').write(
        lambda view: saved.update(view=view, cursor=view.execute('SELECT * FROM v1_projects')))
    with pytest.raises(AuthorityError):
        saved['view'].execute('DELETE FROM v1_projects')
    with pytest.raises(AuthorityError):
        saved['cursor'].fetchall()
    assert not hasattr(saved['cursor'], 'connection')


def test_nested_owner_transaction_rollback_and_read_only(runtime):
    cap = runtime.state.capability('CAB-B')
    with pytest.raises(WriterDenied):
        cap.write(lambda tx: cap.write(lambda nested: nested.execute('SELECT 1')))
    with pytest.raises(RuntimeError, match='inject'):
        def failure(tx):
            tx.execute('INSERT INTO v1_projects VALUES (?,?,?,?,?)', ('rolled-back','owner-A', SHA,'ACTIVE',1))
            raise RuntimeError('inject')
        cap.write(failure)
    assert cap.read(lambda tx: tx.fetch_one('SELECT * FROM v1_projects WHERE project_id=?', ('rolled-back',))) is None
    with pytest.raises((WriterDenied, sqlite3.DatabaseError)):
        cap.read(lambda tx: tx.execute('DELETE FROM v1_projects'))


def test_frozen_state_refuses_existing_legacy_or_mutated_db(tmp_path):
    path = tmp_path/'db'/'db.sqlite'
    path.parent.mkdir(mode=0o700)
    raw = sqlite3.connect(path)
    raw.execute('CREATE TABLE old_legacy(id INTEGER)')
    raw.close()
    path.chmod(0o600)
    with pytest.raises(AuthorityError, match='schema objects mismatch'):
        R9Authority(path)


def test_durable_root_outbox_is_atomic_deduplicated_and_restart_unknown(tmp_path):
    path = tmp_path/'authority'/'db.sqlite'
    message = {'project_id':None,'feature_id':None,'domain':'production.test',
               'event_type':'REQUEST','idempotency_key':'job-A','payload':'abc'}
    with R9Authority(path) as state:
        queue = DurableOutbox(state, CLOCK)
        with state.capability('CAB-B').transaction() as tx:
            queue.append(tx, message)
            queue.append(tx, message)
        assert len(state.outbox_rows()) == 1
        with pytest.raises(IdempotencyConflict):
            with state.capability('CAB-B').transaction() as tx:
                queue.append(tx, {**message,'payload':'DIFFERENT'})
        assert len(state.outbox_rows()) == 1
        assert queue.claim(state.outbox_rows()[0]['message_id'])
        with pytest.raises(WriterDenied):
            queue.acknowledge(state.outbox_rows()[0]['message_id'], provider_receipt_ref='native-receipt')
    with R9Authority(path) as reopened:
        assert [x['state'] for x in reopened.outbox_rows()] == ['UNKNOWN']
        assert not DurableOutbox(reopened, CLOCK).claim(reopened.outbox_rows()[0]['message_id'])


def test_outbox_append_is_rolled_back_with_source_mutation(runtime):
    message = {'domain':'production.test','event_type':'REQUEST',
               'idempotency_key':'rollback-1'}
    count = len(runtime.state.outbox_rows())
    with pytest.raises(RuntimeError, match='abort'):
        with runtime.state.capability('CAB-B').transaction() as tx:
            runtime.outbox.append(tx, message)
            raise RuntimeError('abort')
    assert len(runtime.state.outbox_rows()) == count


def test_unverified_external_ingress_and_readiness_fail_closed(runtime):
    health = runtime.health()
    assert health['liveness'] == 'ALIVE'
    assert health['readiness'] == 'NOT_READY'
    assert health['registered_port_count'] == 22
    assert health['missing_native_dependencies']
    assert runtime.ingress.submit({'auth_proof_ref':'f', 'request_digest':SHA}, 'f').status != 'OK'
    assert runtime.ingress.submit({'auth_proof_ref':'different'}, 'f').status == 'REJECTED'


def test_native_bindings_are_not_self_certifying(tmp_path):
    # Supplying stubs must not claim external host/protocol verification.
    with open_studio(sqlite_path=tmp_path/'db'/'db.sqlite',
                     bindings={'native_worker_host':object(), 'runner':object()}) as root:
        assert root.health()['readiness'] == 'NOT_READY'


def test_cli_doctor_reports_real_fail_closed_status(tmp_path):
    path = tmp_path/'config.json'
    path.write_text(json.dumps({'sqlite_path':str(tmp_path/'db'/'db.sqlite'),
                                'cas_path':str(tmp_path/'cas')}), encoding='utf-8')
    command = [sys.executable, '-m', 'nomad_studio.app', '--doctor', '--config', str(path)]
    with open_studio(sqlite_path=tmp_path/'db'/'db.sqlite', cas_path=tmp_path/'cas'):
        pass
    proc = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False,
                          env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')})
    assert proc.returncode == 2, proc.stderr
    status = json.loads(proc.stdout)
    assert status['database'] == 'R9_FRESH_SCHEMA_VERIFIED'
    assert status['readiness'] == 'NOT_READY'
    assert 'owner-fixture' not in proc.stdout


def test_missing_config_cannot_boot_ready(tmp_path):
    proc = subprocess.run([sys.executable, '-m', 'nomad_studio.app', '--doctor',
                           '--config', str(tmp_path/'missing.json')],
                          capture_output=True, text=True, timeout=20, check=False,
                          env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')})
    assert proc.returncode == 2
    assert json.loads(proc.stdout)['readiness'] == 'NOT_READY'


def test_outbox_root_receipts_and_verified_native_callback(runtime):
    message = {'domain':'test.provider', 'event_type':'REQUEST',
               'idempotency_key':'key-A', 'body':'immutable'}
    with runtime.state.capability('CAB-B').transaction() as tx:
        runtime.outbox.append(tx, message)
    event = runtime.state.outbox_rows()[0]
    receipt_rows = runtime.state.read(lambda tx: tx.fetch_all('SELECT * FROM v1_transaction_receipts'))
    idem_rows = runtime.state.read(lambda tx: tx.fetch_all('SELECT * FROM v1_idempotency'))
    assert len(receipt_rows) == 1 and len(idem_rows) == 1
    assert idem_rows[0]['request_digest'] == event['payload_digest']
    assert runtime.outbox.claim(event['message_id'])
    assert not runtime.outbox.claim(event['message_id'])
    with pytest.raises(WriterDenied):
        runtime.outbox.acknowledge(event['message_id'], provider_receipt_ref='proof-1')
    # Negative receipt response cannot ACK and does not mutate state.
    unverified = DurableOutbox(runtime.state, CLOCK,
        readback=lambda mid,proof: {'status':'VERIFIED','message_id':mid,
                                    'receipt_ref':proof,'payload_digest':'wrong'})
    with pytest.raises(WriterDenied):
        unverified.acknowledge(event['message_id'], provider_receipt_ref='proof-1')
    assert runtime.state.outbox_rows()[0]['state'] == 'CLAIMED'
    verified = DurableOutbox(runtime.state, CLOCK,
        readback=lambda mid,proof: {'status':'VERIFIED','message_id':mid,
                                    'receipt_ref':proof,'payload_digest':event['payload_digest']})
    assert verified.acknowledge(event['message_id'], provider_receipt_ref='proof-1')
    assert runtime.state.outbox_rows()[0]['state'] == 'ACKED'
    assert not verified.acknowledge(event['message_id'], provider_receipt_ref='proof-1')


def test_native_timeout_marks_external_effect_unknown(runtime):
    with runtime.state.capability('CAB-B').transaction() as tx:
        runtime.outbox.append(tx, {'domain':'test.provider','event_type':'ATTEMPT','idempotency_key':'timeout'})
    event = runtime.state.outbox_rows()[0]
    runtime.outbox.claim(event['message_id'])
    def timed_out(_msg, _receipt):
        raise TimeoutError('provider did not return a trustworthy result')
    provider = DurableOutbox(runtime.state, CLOCK, readback=timed_out)
    assert provider.acknowledge(event['message_id'], provider_receipt_ref='native-proof') is False
    assert runtime.state.outbox_rows()[0]['state'] == 'UNKNOWN'
    assert not runtime.outbox.claim(event['message_id'])


def test_foreign_database_identity_cannot_be_repurposed(tmp_path):
    path = tmp_path/'sealed'/'db.sqlite'
    path.parent.mkdir(mode=0o700)
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA application_id=123456')
    conn.close()
    path.chmod(0o600)
    with pytest.raises(AuthorityError, match='foreign database identity'):
        R9Authority(path)


def test_doctor_observes_without_acquiring_second_writer(tmp_path):
    path = tmp_path/'config.json'
    db = tmp_path/'authority'/'db.sqlite'
    path.write_text(json.dumps({'sqlite_path':str(db), 'cas_path':str(tmp_path/'cas')}))
    with open_studio(sqlite_path=db) as runtime:
        cmd = [sys.executable, '-m', 'nomad_studio.app', '--doctor', '--config',str(path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False,
            env={**os.environ, 'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src')})
        assert result.returncode == 2
        observed = json.loads(result.stdout)
        assert observed['database'] == 'R9_FRESH_SCHEMA_VERIFIED'
        assert observed['liveness'] == 'NOT_OBSERVED'
        assert 'OwnerLocked' not in result.stdout
        assert runtime.health()['liveness'] == 'ALIVE'


def test_outbox_dispatch_never_delivers_without_native_receipt(tmp_path):
    sent=[]
    with open_studio(sqlite_path=tmp_path/'db'/'db.sqlite', clock_us=CLOCK,
                     bindings={'native_event_delivery':lambda envelope: sent.append(envelope)}) as runtime:
        with runtime.state.capability('CAB-B').transaction() as tx:
            runtime.outbox.append(tx, {'domain':'production.director',
                'event_type':'FEATURE_RUN_TRANSITION','idempotency_key':'event-1'})
        msg_id=runtime.state.outbox_rows()[0]['message_id']
        result=runtime.dispatch_once(msg_id)
        assert result.status == 'NOT_VERIFIED'
        assert sent == []
        assert runtime.state.outbox_rows()[0]['state'] == 'PENDING'


def test_outbox_dispatch_exact_verified_receipt_and_no_replay(tmp_path):
    sends=[]
    def delivery(payload):
        sends.append(payload)
        return Result('OK','OK',receipt_ref='provider-receipt-1')
    with open_studio(sqlite_path=tmp_path/'db'/'db.sqlite', clock_us=CLOCK,
        bindings={'native_event_delivery':delivery,
                  'native_outbox_readback':lambda mid,ref: {
                      'status':'VERIFIED','message_id':mid,'receipt_ref':ref,
                      'payload_digest':runtime.state.outbox_rows()[0]['payload_digest']}}) as runtime:
        with runtime.state.capability('CAB-B').transaction() as tx:
            runtime.outbox.append(tx, {'domain':'production.director',
                'event_type':'FEATURE_RUN_TRANSITION', 'idempotency_key':'event-2'})
        msg_id=runtime.state.outbox_rows()[0]['message_id']
        assert runtime.dispatch_once(msg_id).status == 'OK'
        assert runtime.state.outbox_rows()[0]['state'] == 'ACKED'
        assert runtime.dispatch_once(msg_id).status == 'CONFLICT'
        assert len(sends)==1


def test_outbox_dispatch_timeout_is_unknown_and_not_replayed(tmp_path):
    calls=[]
    def timeout(envelope):
        calls.append(envelope)
        raise TimeoutError('provider could have applied the effect')
    with open_studio(sqlite_path=tmp_path/'db'/'db.sqlite', clock_us=CLOCK,
        bindings={'native_event_delivery':timeout,
                  'native_outbox_readback':lambda mid,ref: None}) as runtime:
        with runtime.state.capability('CAB-B').transaction() as tx:
            runtime.outbox.append(tx, {'domain':'production.director',
                'event_type':'FEATURE_RUN_TRANSITION','idempotency_key':'event-3'})
        msg_id=runtime.state.outbox_rows()[0]['message_id']
        assert runtime.dispatch_once(msg_id).status == 'UNKNOWN_EXTERNAL'
        assert runtime.state.outbox_rows()[0]['state'] == 'UNKNOWN'
        assert runtime.dispatch_once(msg_id).status == 'CONFLICT'
        assert len(calls)==1


def test_no_v1_component_or_legacy_store_imports():
    import importlib.util
    for name in ('nomad_studio.contracts.v1','nomad_studio.state.api',
                 'nomad_studio.state.store','nomad_studio.state.schema',
                 'nomad_studio.archive','nomad_studio.source','nomad_studio.knowledge',
                 'nomad_studio.publication.api','nomad_studio.artifacts.api'):
        try:
            found = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            found = None
        assert found is None, f'legacy module remains importable: {name}'
