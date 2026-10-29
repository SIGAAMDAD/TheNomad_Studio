"""Frozen Component interface for SQLite authority operations, and open_store."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
import threading
import time
from typing import Any, Mapping

from nomad_studio.contracts.v1 import (CommandEnvelope, Component, DomainEvent,
                                        Outcome, QueryEnvelope, VerifiedIdentity)
from .errors import StateError, StaleAssignment
from .store import SQLiteStateStore, open_store


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(data: Any) -> str:
    return hashlib.sha256(_canonical(data).encode('utf-8')).hexdigest()


def _out(state: str, code: str, *, receipt: str | None = None, **payload: Any) -> Outcome:
    return Outcome(state=state, code=code, receipt_id=receipt, payload=payload)


class StateComponent(Component):
    name = 'state'

    def __init__(self, store: SQLiteStateStore, *, snapshot_dir: str | None = None):
        self.store = store
        self.snapshot_dir = Path(snapshot_dir).absolute() if snapshot_dir else None
        self._snapshot_lock = threading.RLock()

    def _authorized(self, identity: VerifiedIdentity, permission: str, *, mutation: bool) -> Outcome | None:
        if not isinstance(identity, VerifiedIdentity) or not identity.principal_id or not identity.credential_id or not identity.issuer_id or not identity.authenticated_via:
            return _out('DENIED', 'IDENTITY_UNVERIFIED')
        if identity.expires_at_us <= time.time_ns() // 1000:
            return _out('DENIED', 'EXPIRED_GRANT')
        if not any(scope in identity.scopes for scope in (permission, 'state:admin', 'state:*')):
            return _out('DENIED', 'INVALID_SCOPE')
        # For mutations an epoch row is mandatory; the trusted issuer registers
        # and rotates it via SQLiteStateStore.set_actor_epoch().
        if mutation:
            try:
                live = self.store.actor_epoch(identity.principal_id)
            except StateError as exc:
                return _out('BLOCKED', exc.code)
            if live is None or live != identity.current_authority_epoch:
                return _out('DENIED', 'STALE_ASSIGNMENT')
        return None

    def execute(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        if not isinstance(command, CommandEnvelope) or command.version != 'NOMAD/CMD/1' or command.domain != 'state' or command.operation != 'snapshot' or not isinstance(command.request_id, str) or not command.request_id or not isinstance(command.idempotency_key, str) or not command.idempotency_key or not isinstance(command.payload, Mapping) or command.payload:
            return _out('DENIED', 'MALFORMED_REQUEST')
        denied = self._authorized(identity, 'state:snapshot', mutation=True)
        if denied:
            return denied
        if self.snapshot_dir is None or not self.snapshot_dir.is_dir():
            return _out('BLOCKED', 'NOT_CONFIGURED')
        # Fixed filename derived from request; a caller cannot choose a path.
        request_digest = _hash({'principal': identity.principal_id, 'request_id': command.request_id})
        target = self.snapshot_dir / ('snapshot-' + request_digest + '.sqlite3')
        fingerprint = _hash({'operation': 'state.snapshot', 'principal_id': identity.principal_id,
                             'request_id': command.request_id, 'payload': dict(command.payload),
                             'authority_epoch': identity.current_authority_epoch,
                             'idempotency_key_sha256': hashlib.sha256(command.idempotency_key.encode()).hexdigest()})
        prefix = 'state:snapshot:' + command.idempotency_key

        def require_epoch(tx):
            row = tx.fetch_one('SELECT epoch FROM state_actor_epochs WHERE principal_id=?',
                               (identity.principal_id,))
            if row is None or row['epoch'] != identity.current_authority_epoch:
                raise StaleAssignment('principal epoch changed before mutation commit')

        def intent(tx):
            require_epoch(tx)
            event_id = 'state:snapshot:intent:' + request_digest
            tx.append_event(DomainEvent(event_id, 'state', 'SNAPSHOT_REQUESTED',
                                        identity.principal_id, 0, correlation_id=command.request_id,
                                        payload={'request_id': command.request_id, 'command_fingerprint': fingerprint}))
            receipt = tx.record_receipt('state', command.request_id, event_id,
                                        {'status': 'PENDING'})
            return {'receipt': receipt}

        try:
            intent_result = self.store.transact_domain('state', intent,
                           idempotency_key=prefix + ':intent', fingerprint=fingerprint)
        except StateError as exc:
            state = 'CONFLICT' if exc.code == 'IDEMPOTENCY_CONFLICT' else 'DENIED' if exc.code == 'STALE_ASSIGNMENT' else 'UNKNOWN' if exc.code == 'PROVIDER_UNKNOWN' else 'BLOCKED'
            return _out(state, exc.code, **({'pending': True} if state == 'UNKNOWN' else {}))
        # Never hold a SQLite write transaction during file copy/verification.
        try:
            with self._snapshot_lock:
                if target.is_file():
                    proof = self.store.inspect_snapshot(target)
                else:
                    proof = self.store.backup_snapshot(target)
                proof = {k: v for k, v in proof.items() if k != 'path'}
                proof['snapshot_ref'] = target.name  # opaque basename only

                def completed(tx):
                    require_epoch(tx)
                    event_id = 'state:snapshot:verified:' + request_digest
                    tx.append_event(DomainEvent(event_id, 'state', 'SNAPSHOT_VERIFIED',
                            identity.principal_id, 0, correlation_id=command.request_id,
                            causation_id='state:snapshot:intent:' + request_digest,
                            payload={'request_id': command.request_id, 'sha256': proof['sha256'],
                                     'bytes': proof['bytes'], 'snapshot_ref': target.name}))
                    receipt = tx.record_receipt('state', command.request_id + ':verified',
                                                event_id, proof)
                    return {'receipt': receipt, 'proof': proof}

                final = self.store.transact_domain('state', completed,
                          idempotency_key=prefix + ':verified', fingerprint=fingerprint)
                if final['proof']['sha256'] != proof['sha256'] or final['proof']['bytes'] != proof['bytes']:
                    return _out('CONFLICT', 'DOCUMENT_CONFLICT', receipt=intent_result['receipt'], pending=True)
                return _out('ACCEPTED', 'OK', receipt=final['receipt'], **final['proof'])
        except StateError as exc:
            return _out('CONFLICT' if exc.code == 'IDEMPOTENCY_CONFLICT' else 'DENIED' if exc.code == 'STALE_ASSIGNMENT' else 'UNKNOWN', exc.code,
                        receipt=intent_result['receipt'], pending=True)
        except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
            return _out('UNKNOWN', 'PROVIDER_UNKNOWN', receipt=intent_result['receipt'], pending=True)

    def query(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        if (not isinstance(query, QueryEnvelope) or query.version != 'NOMAD/QUERY/1' or
                query.domain != 'state' or not query.request_id or
                query.operation not in ('health', 'schema', 'events') or
                type(query.limit) is not int or not 1 <= query.limit <= 500 or
                not isinstance(query.payload, Mapping)):
            return _out('DENIED', 'MALFORMED_REQUEST')
        valid_keys = {'domain'} if query.operation == 'events' else set()
        if set(query.payload) - valid_keys or (query.cursor is not None and query.operation != 'events'):
            return _out('DENIED', 'INVALID_SCHEMA')
        permission = 'state:audit' if query.operation == 'events' else 'state:read'
        denied = self._authorized(identity, permission, mutation=False)
        if denied:
            return denied
        try:
            if query.operation == 'health':
                diagnostics = dict(self.store.diagnostics())
                return _out('ACCEPTED', 'OK', **diagnostics)
            if query.operation == 'schema':
                return _out('ACCEPTED', 'OK', **self.store.schema_identity())
            domain = query.payload.get('domain')
            if domain is not None and (not isinstance(domain, str) or not domain.isidentifier()):
                return _out('DENIED', 'INVALID_SCHEMA')
            if query.cursor is None:
                after = 0
            else:
                if not isinstance(query.cursor, str) or not query.cursor.isascii() or not query.cursor.isdecimal() or len(query.cursor) > 20:
                    return _out('DENIED', 'INVALID_SCHEMA')
                after = int(query.cursor)
            def list_events(rd):
                sql = '''SELECT sequence,event_id,domain,kind,actor_principal_id,issued_at_us,
                    correlation_id,causation_id,job_id,shift_id,assignment_epoch,
                    payload_sha256,payload_json,source_evidence_ref FROM state_events
                    WHERE sequence > ?'''
                params: list[Any] = [after]
                if domain is not None:
                    sql += ' AND domain=?'
                    params.append(domain)
                sql += ' ORDER BY sequence ASC LIMIT ?'
                params.append(query.limit)
                rows = rd.fetch_all(sql, params)
                return [dict(row, payload=json.loads(row['payload_json'])) for row in rows]
            events = self.store.read(list_events)
            for item in events:
                item.pop('payload_json', None)
            return _out('ACCEPTED', 'OK', events=events, next_cursor=str(events[-1]['sequence']) if events else str(after))
        except StateError as exc:
            return _out('BLOCKED', exc.code)


def build_component(deps: Mapping[str, object]) -> Component:
    """Integrator passes the singleton StateStore; only composition root opens it."""
    store = deps.get('state_store', deps.get('store'))
    if not isinstance(store, SQLiteStateStore):
        raise TypeError('state_store must be the singleton SQLiteStateStore')
    snapshot_dir = deps.get('snapshot_dir')
    if snapshot_dir is not None and not isinstance(snapshot_dir, (str, Path)):
        raise TypeError('snapshot_dir must be a path')
    return StateComponent(store, snapshot_dir=str(snapshot_dir) if snapshot_dir is not None else None)
