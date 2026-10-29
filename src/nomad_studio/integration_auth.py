"""Trusted, process-private cross-module policy adapter.

WG-B's public `trust.authorize` is deliberately NOT a wire-accessible oracle.
WG-D/I/O instead consume this Component-shaped internal dependency. It reads
actual WG-B session/epoch and WG-D/WG-E facts and obtains independent external
review attestations only from a configured trusted host port. It is NEVER
registered in the public gateway's component registry.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Mapping

from nomad_studio.contracts.v1 import CommandEnvelope, Outcome, QueryEnvelope, VerifiedIdentity


class InternalAuthorization:
    name = 'trust'

    def __init__(self, *, store: Any, trust: Any,
                 slot_authority: Callable[[str], Mapping[str, Any] | None],
                 clock_us: Callable[[], int], review_verifier: Any = None):
        self.store = store
        self.trust = trust
        self.slot_authority = slot_authority
        self.clock_us = clock_us
        self.review_verifier = review_verifier

    @staticmethod
    def deny(code: str = 'IDENTITY_UNVERIFIED', state: str = 'DENIED') -> Outcome:
        return Outcome(state, code)

    def _valid(self, identity: VerifiedIdentity) -> bool:
        return (isinstance(identity, VerifiedIdentity) and bool(identity.principal_id) and
                bool(identity.credential_id) and bool(identity.issuer_id) and
                identity.expires_at_us > self.clock_us() and
                type(identity.current_authority_epoch) is int and identity.current_authority_epoch > 0)

    def _slot(self, worker: str) -> Mapping[str, Any] | None:
        try:
            result = self.slot_authority(worker)
            return result if isinstance(result, Mapping) else None
        except Exception:
            return None

    def _session(self, identity: VerifiedIdentity, worker: str) -> Mapping[str, Any] | None:
        if (not self._valid(identity) or identity.authenticated_via != 'attenuated_session' or
                identity.principal_id != 'worker/' + worker or
                not identity.credential_id.startswith('session/')):
            return None
        sid = identity.credential_id.removeprefix('session/')
        if not re.fullmatch('[a-f0-9]{32}', sid):
            return None
        def select(reader: Any) -> Mapping[str, Any] | None:
            row = reader.fetch_one('''SELECT s.*, w.revoked AS worker_revoked,
                w.authority_epoch AS live_epoch, l.scope_digest AS scope_digest
                FROM trust_sessions s JOIN trust_workers w ON w.worker_id=s.worker_id
                JOIN trust_launches l ON l.ticket_id=s.ticket_id
                WHERE s.session_id=?''', (sid,))
            return dict(row) if row else None
        try:
            row = self.store.read(select)
        except Exception:
            return None
        if not row or row['worker_id'] != worker or row['revoked'] or row['worker_revoked'] or row['expires_at_us'] <= self.clock_us():
            return None
        if (row['authority_epoch'] != identity.current_authority_epoch or
                row['authority_epoch'] != row['live_epoch']):
            return None
        if set(identity.scopes) - set(json.loads(row['scopes_json'])):
            return None
        slot = self._slot(worker)
        if (not slot or slot.get('epoch') != row['authority_epoch'] or
                slot.get('role_id') != row['role_id'] or
                row['job_id'] not in slot.get('jobs', ()) or
                slot.get('scope_digest') != row['scope_digest'] or
                row['connector_credential_id'] not in slot.get('connector_credential_ids', ())):
            return None
        return row

    def assignment_epoch(self, job_id: str) -> int:
        row = self.store.read(lambda r: r.fetch_one('SELECT assignment_epoch FROM scheduler_jobs WHERE job_id=?', (job_id,)))
        return int(row['assignment_epoch']) if row is not None else -1

    def _shift_authorize(self, payload: Mapping[str, Any], identity: VerifiedIdentity) -> Outcome:
        required = {'action','worker_id','response_id','authority_epoch'}
        if set(payload) - (required | {'job_id','assignment_epoch'}) or not required <= set(payload):
            return self.deny('INVALID_SCHEMA')
        worker, response = payload['worker_id'], payload['response_id']
        action = payload['action']
        if not isinstance(worker, str) or not isinstance(response, str) or type(payload['authority_epoch']) is not int:
            return self.deny('INVALID_SCHEMA')
        if payload['authority_epoch'] != identity.current_authority_epoch:
            return self.deny('STALE_ASSIGNMENT')
        if action == 'shift:control':
            if (identity.authenticated_via != 'mtls' or
                    not identity.principal_id.startswith('owner/') or
                    'shift:control' not in identity.scopes):
                return self.deny('OWNER_APPROVAL_REQUIRED')
            try:
                live = self.store.actor_epoch(identity.principal_id)
            except Exception:
                return self.deny('PROVIDER_UNKNOWN', 'UNKNOWN')
            if live != identity.current_authority_epoch:
                return self.deny('STALE_ASSIGNMENT')
            slot = self._slot(worker)
            if slot is None:
                return self.deny('NOT_CONFIGURED','BLOCKED')
            if slot.get('epoch') != payload['authority_epoch']:
                return self.deny('STALE_ASSIGNMENT')
        elif action in {'shift:operate','shift:read'}:
            session = self._session(identity, worker)
            if session is None or session['shift_id'] != response:
                return self.deny('IDENTITY_UNVERIFIED')
            if action == 'shift:operate' and not any(s in identity.scopes for s in (
                'shift.start','shift.next','shift.record','shift.checkpoint','shift.finish','shift.yield')):
                return self.deny('INVALID_SCOPE')
            if action == 'shift:read' and not any(s in identity.scopes for s in (
                'shift.start','shift.checkpoint','shift.next','shift.record')):
                return self.deny('INVALID_SCOPE')
            if payload.get('job_id') is not None and payload['job_id'] != session['job_id']:
                # Each session's START job is pinned. Later jobs are checked by
                # scheduler epoch/lease and gateway operation on each new claim.
                job = self.store.read(lambda r: r.fetch_one(
                    'SELECT assigned_worker_id,assignment_epoch FROM scheduler_jobs WHERE job_id=?',
                    (payload['job_id'],)))
                if (not job or job['assigned_worker_id'] != worker or
                        job['assignment_epoch'] != payload.get('assignment_epoch')):
                    return self.deny('STALE_ASSIGNMENT')
        else:
            return self.deny('INVALID_SCHEMA')
        try:
            row = self.store.read(lambda r: r.fetch_one(
                'SELECT control_epoch,state,worker_id,authority_epoch FROM shift_responses WHERE response_id=?',
                (response,)))
        except Exception:
            return self.deny('PROVIDER_UNKNOWN','UNKNOWN')
        if row and (row['worker_id'] != worker or row['authority_epoch'] != identity.current_authority_epoch):
            return self.deny('STALE_ASSIGNMENT')
        return Outcome('ACCEPTED','AUTHORIZED',payload={
            'worker_id':worker,'authority_epoch':identity.current_authority_epoch,
            'control_epoch':int(row['control_epoch']) if row else 0,
            'control_state':str(row['state']) if row else 'RUNNING',
        })

    def _console_authorize(self, payload: Mapping[str, Any], identity: VerifiedIdentity) -> Outcome:
        if set(payload) != {'scope','worker_id','assignment_epoch','authority_epoch'}:
            return self.deny('INVALID_SCHEMA')
        scope, worker, epoch = payload['scope'], payload['worker_id'], payload['assignment_epoch']
        if not isinstance(worker,str) or type(epoch) is not int or epoch < 1:
            return self.deny('INVALID_SCHEMA')
        if (scope not in {'owner:console:launch','owner:console:control'} or
                identity.authenticated_via != 'mtls' or
                not identity.principal_id.startswith('owner/') or
                'owner' not in identity.scopes or scope not in identity.scopes or
                payload['authority_epoch'] != identity.current_authority_epoch):
            return self.deny('OWNER_APPROVAL_REQUIRED')
        try:
            live = self.store.actor_epoch(identity.principal_id)
        except Exception:
            return self.deny('PROVIDER_UNKNOWN','UNKNOWN')
        if live != identity.current_authority_epoch:
            return self.deny('STALE_ASSIGNMENT')
        slot = self._slot(worker)
        if slot is None:
            return self.deny('NOT_CONFIGURED','BLOCKED')
        if slot.get('epoch') != epoch:
            return self.deny('STALE_ASSIGNMENT')
        return Outcome('ACCEPTED','AUTHORIZED',payload={'authorized':True,
            'authority_epoch':identity.current_authority_epoch,
            'grant_id':'local.' + identity.principal_id.replace('/','.') + '.' + worker + '.' + str(epoch)})

    def execute(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        if not isinstance(command,CommandEnvelope) or command.version != 'NOMAD/CMD/1' or command.domain != 'trust' or not self._valid(identity):
            return self.deny('MALFORMED_REQUEST')
        if command.operation == 'trust.issue_launch':
            return self.trust.execute(command, identity)
        if command.operation not in {'authorize', 'trust.authorize'}:
            return self.deny('NOT_SUPPORTED')
        if not isinstance(command.payload, Mapping):
            return self.deny('INVALID_SCHEMA')
        if 'action' in command.payload:
            return self._shift_authorize(command.payload, identity)
        if 'scope' in command.payload:
            return self._console_authorize(command.payload, identity)
        return self.deny('INVALID_SCHEMA')

    def query(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        if (not isinstance(query,QueryEnvelope) or query.version != 'NOMAD/QUERY/1' or
                query.domain != 'trust' or query.operation != 'trust.authorize' or not self._valid(identity) or
                not isinstance(query.payload,Mapping)):
            return self.deny('MALFORMED_REQUEST')
        p = query.payload
        required = {'action','principal_id','credential_id','authority_epoch','job_id','department','repo','branch','source_sha'}
        if set(p) - (required | {'decision_refs'}) or not required <= set(p):
            return self.deny('INVALID_SCHEMA')
        role = {'qa_verdict':('qa/','qa'), 'integration':('integrator/','integrator'),
                'head_merge':('head/','department_head')}.get(p['action'])
        if (role is None or identity.authenticated_via != 'mtls' or
                not identity.principal_id.startswith(role[0]) or
                p['principal_id'] != identity.principal_id or
                p['credential_id'] != identity.credential_id or
                p['authority_epoch'] != identity.current_authority_epoch):
            return self.deny('INVALID_SCOPE')
        scope = {'qa_verdict':'review:verdict','integration':'review:integration',
                 'head_merge':'review:merge_intent'}[p['action']]
        if scope not in identity.scopes:
            return self.deny('INVALID_SCOPE')
        if self.review_verifier is None:
            return self.deny('NOT_CONFIGURED', 'NOT_RUN')
        try:
            proof = self.review_verifier(identity, dict(p))
        except Exception:
            return self.deny('PROVIDER_UNKNOWN','UNKNOWN')
        if (not isinstance(proof,Mapping) or proof.get('provider_verified') is not True or
                proof.get('allowed') is not True or proof.get('role') != role[1] or
                any(proof.get(k) != p[k] for k in required if k!='action') or
                proof.get('action') != p['action'] or not isinstance(proof.get('grant_receipt'),str) or
                not proof['grant_receipt']):
            return self.deny('IDENTITY_UNVERIFIED')
        if 'decision_refs' in p:
            dec=proof.get('owner_decisions')
            if not isinstance(dec,Mapping) or set(p['decision_refs']) - set(dec):
                return self.deny('OWNER_APPROVAL_REQUIRED')
            for kind, ref in p['decision_refs'].items():
                entry=dec[kind]
                if (not isinstance(entry,Mapping) or entry.get('ref') != ref or
                        entry.get('verified') is not True or entry.get('signature_verified') is not True or
                        entry.get('source_sha') != p['source_sha'] or
                        not isinstance(entry.get('owner_principal_id'),str) or
                        not entry['owner_principal_id'].startswith('owner/')):
                    return self.deny('OWNER_APPROVAL_REQUIRED')
        return Outcome('ACCEPTED','AUTHORIZED',payload=dict(proof))

    def authorize_relay_worker(self, identity: VerifiedIdentity, worker: str,
                               epoch: int, operation: str) -> bool:
        if operation not in {'poll','ack','retry','dead_letter'} or 'relay.'+operation not in identity.scopes:
            return False
        row=self._session(identity,worker)
        return row is not None and row['authority_epoch']==epoch

    def authorize_relay_sender(self, identity: VerifiedIdentity, worker: str,
                               epoch: int, kind: str) -> bool:
        if (not self._valid(identity) or identity.authenticated_via != 'mtls' or
                not identity.principal_id.startswith('owner/') or 'relay.send' not in identity.scopes or
                kind not in {'CONTROL','PROMPT_BATCH'}):
            return False
        try:
            if self.store.actor_epoch(identity.principal_id)!=identity.current_authority_epoch:
                return False
        except Exception:
            return False
        slot=self._slot(worker)
        return slot is not None and slot.get('epoch')==epoch

    def verify_relay_receipt(self, identity: VerifiedIdentity, worker: str,
                             epoch: int, message_id: str, receipt_id: str) -> bool:
        # A reported receipt ID alone is never enough. It must be a real durable
        # shift report whose evidence_refs explicitly bind this exact message.
        if self._session(identity,worker) is None:
            return False
        try:
            def read(r: Any):
                return r.fetch_one('''SELECT x.report_json,a.assignment_epoch,s.worker_id
                    FROM shift_job_receipts x
                    JOIN shift_attempts a ON a.attempt_id=x.attempt_id
                    JOIN shift_responses s ON s.response_id=x.response_id
                    WHERE x.receipt_id=?''', (receipt_id,))
            row=self.store.read(read)
            return bool(row and row['worker_id']==worker and row['assignment_epoch']==epoch and
                        message_id in json.loads(row['report_json']).get('evidence_refs',()))
        except Exception:
            return False
