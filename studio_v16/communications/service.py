"""Bounded indexed add-on APIs. Policy is injected by the isolated service owner."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from studio_v16.db.authority_contract import Denied, GatewayContext, _fresh
from studio_v16.db.sqlite.publication import row
from studio_v16.db.sqlite.types import canonical, parse_json, integer


@dataclass(frozen=True)
class Scope:
    departments: frozenset[str]
    operations: frozenset[str]
    owner: bool = False


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class CoordinationService:
    def __init__(self, store, *, policy: dict[str, Scope]):
        self.store = store
        self.owner = store.owner
        self._policy = dict(policy)

    def _auth(self, conn, context, department, operation):
        self.store._principal(conn, context)
        scope = self._policy.get(context.principal_id)
        if scope is None or department not in scope.departments or operation not in scope.operations:
            raise Denied('ACCESS_REQUIRED')
        return scope

    @staticmethod
    def _leader(conn, department, principal_id, epoch):
        leader = row(conn, 'SELECT * FROM leader_state WHERE department=?', (department,))
        if (leader is None or leader['principal_id'] != principal_id or leader['leader_epoch'] != epoch
                or leader['delegation_paused']):
            raise Denied('STALE_ASSIGNMENT')
        return leader

    def _decision(self, conn, context, decision_id, action, binding, now):
        d = row(conn, '''SELECT d.*,p.status AS approver_status FROM decisions d JOIN principals p
            ON p.principal_id=d.authority_principal_id WHERE d.decision_id=?''', (decision_id,))
        policy = self._policy.get(d['authority_principal_id']) if d else None
        if (d is None or policy is None or not policy.owner or d['approver_status'] != 'ACTIVE'
                or d['revoked_at'] is not None or (d['expires_at'] is not None and d['expires_at'] <= now)
                or d['action'] != action or d['approved_scope_hash'] != sha(binding)
                or not d['proof_ref']):
            raise Denied('OWNER_APPROVAL_REQUIRED')
        return d

    def route_event(self, event_id, department, recipient_id, metadata, context, *, correlation_id=None,
                    causation_id=None, legacy_archive_ref=None):
        raw = canonical(metadata)
        if len(raw.encode()) > 16384:
            raise ValueError('bounded metadata only; long documents belong in Drive')
        with self.owner.write() as (conn, now):
            self._auth(conn, context, department, 'ROUTE')
            if not conn.execute("SELECT 1 FROM principals WHERE principal_id=? AND status='ACTIVE'", (recipient_id,)).fetchone():
                raise Denied('ACCESS_REQUIRED')
            old = row(conn, '''SELECT * FROM department_events WHERE event_id=?
                AND recipient_department=? AND recipient_id=?''', (event_id, department, recipient_id))
            if old:
                if (old['metadata_json'] != raw or old['sender_principal_id'] != context.principal_id
                        or (old['correlation_id'], old['causation_id'], old['legacy_archive_ref']) !=
                        (correlation_id, causation_id, legacy_archive_ref)):
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return old['sequence']
            if not conn.execute('SELECT 1 FROM event_journal WHERE event_id=?', (event_id,)).fetchone():
                raise Denied('ACCESS_REQUIRED')
            return conn.execute('''INSERT INTO department_events(event_id,sender_principal_id,recipient_department,
                recipient_id,correlation_id,causation_id,legacy_archive_ref,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?) RETURNING sequence''',
                (event_id, context.principal_id, department, recipient_id, correlation_id, causation_id,
                 legacy_archive_ref, raw, now)).fetchone()[0]

    def inbox(self, department, context, *, limit=50):
        integer(limit, minimum=1, maximum=200)
        with self.owner.write() as (conn, now):
            self._auth(conn, context, department, 'INBOX')
            previous = conn.execute('''SELECT last_sequence FROM department_inbox_cursors
                WHERE recipient_department=? AND recipient_id=?''', (department, context.principal_id)).fetchone()
            cursor = previous[0] if previous else 0
            result = conn.execute('''SELECT sequence,event_id,metadata_json,correlation_id,causation_id,legacy_archive_ref
                FROM department_events WHERE recipient_department=? AND recipient_id=? AND sequence>?
                ORDER BY sequence LIMIT ?''', (department, context.principal_id, cursor, limit)).fetchall()
            for item in result:
                conn.execute('INSERT INTO department_deliveries VALUES(?,?,?,?) ON CONFLICT DO NOTHING',
                             (department, context.principal_id, item[0], now))
            return [dict(sequence=r[0], event_id=r[1], metadata=parse_json(r[2]), correlation_id=r[3],
                         causation_id=r[4], legacy_archive_ref=r[5]) for r in result]

    def ack(self, department, sequence, context):
        integer(sequence, minimum=1)
        with self.owner.write() as (conn, now):
            self._auth(conn, context, department, 'INBOX')
            previous = conn.execute('''SELECT last_sequence FROM department_inbox_cursors
                WHERE recipient_department=? AND recipient_id=?''', (department, context.principal_id)).fetchone()
            cursor = previous[0] if previous else 0
            delivered = conn.execute('''SELECT 1 FROM department_deliveries
                WHERE recipient_department=? AND recipient_id=? AND sequence=?''',
                (department, context.principal_id, sequence)).fetchone()
            if not delivered:
                raise Denied('ACCESS_REQUIRED')
            if sequence <= cursor:
                return cursor
            next_event = conn.execute('''SELECT sequence FROM department_events WHERE recipient_department=?
                AND recipient_id=? AND sequence>? ORDER BY sequence LIMIT 1''',
                (department, context.principal_id, cursor)).fetchone()
            if not next_event or next_event[0] != sequence:
                raise Denied('RETRYABLE_BLOCKED')
            conn.execute('''INSERT INTO department_inbox_cursors VALUES(?,?,?,?)
                ON CONFLICT(recipient_department,recipient_id) DO UPDATE
                SET last_sequence=excluded.last_sequence,updated_at=excluded.updated_at
                WHERE department_inbox_cursors.last_sequence<?''',
                (department, context.principal_id, sequence, now, sequence))
            return sequence

    def handoff(self, department, new_principal_id, expected_epoch, decision_id, context):
        integer(expected_epoch, minimum=0)
        with self.owner.write() as (conn, now):
            scope = self._auth(conn, context, department, 'HANDOFF')
            if not scope.owner or new_principal_id == context.principal_id:
                raise Denied('OWNER_APPROVAL_REQUIRED')
            binding = dict(department=department, new_principal_id=new_principal_id, expected_epoch=expected_epoch)
            decision = self._decision(conn, context, decision_id, 'LEADER_HANDOFF', binding, now)
            if decision['authority_principal_id'] != context.principal_id:
                raise Denied('OWNER_APPROVAL_REQUIRED')
            new_scope = self._policy.get(new_principal_id)
            if (not new_scope or department not in new_scope.departments
                    or not conn.execute("SELECT 1 FROM principals WHERE principal_id=? AND status='ACTIVE'", (new_principal_id,)).fetchone()):
                raise Denied('ACCESS_REQUIRED')
            current = row(conn, 'SELECT * FROM leader_state WHERE department=?', (department,))
            if (current['leader_epoch'] if current else 0) != expected_epoch:
                raise Denied('STALE_ASSIGNMENT')
            if current:
                self.store._changed(conn.execute('''UPDATE leader_state SET principal_id=?,leader_epoch=?,
                    delegation_paused=0,updated_at=? WHERE department=? AND leader_epoch=?''',
                    (new_principal_id, expected_epoch + 1, now, department, expected_epoch)))
            else:
                conn.execute('INSERT INTO leader_state VALUES(?,?,?,0,?)', (department, new_principal_id, 1, now))
            conn.execute('''INSERT INTO leader_handoffs(department,old_principal_id,new_principal_id,old_epoch,new_epoch,
                decision_id,approver_principal_id,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                (department, current['principal_id'] if current else None, new_principal_id, expected_epoch,
                 expected_epoch + 1, decision_id, context.principal_id, now))
            return expected_epoch + 1

    def register_document(self, file_id, revision, exported_sha256, department, owner_id, readers, source_ref, context):
        if not isinstance(readers, list) or not readers or any(not isinstance(v, str) or not v for v in readers):
            raise ValueError('explicit reader principal IDs required')
        with self.owner.write() as (conn, now):
            scope = self._auth(conn, context, department, 'DOCUMENT_REGISTER')
            if not scope.owner or not context.provider_readback_verified:
                raise Denied('ACCESS_REQUIRED')
            leader = row(conn, 'SELECT leader_epoch FROM leader_state WHERE department=?', (department,))
            if leader is None:
                raise Denied('STALE_ASSIGNMENT')
            expected = (file_id, revision, exported_sha256, owner_id, department, canonical(sorted(set(readers))), source_ref)
            prior = conn.execute('''SELECT file_id,revision,exported_sha256,owner_principal_id,department,reader_scope_json,source_ref
                FROM document_refs WHERE file_id=? AND revision=?''', (file_id, revision)).fetchone()
            if prior:
                if prior != expected:
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return revision
            conn.execute('INSERT INTO document_refs VALUES(?,?,?,?,?,?,?,?)', (*expected, now))
            conn.execute('''INSERT INTO document_heads(file_id,revision,leader_epoch) VALUES(?,?,?)
                ON CONFLICT(file_id) DO NOTHING''', (file_id, revision, leader['leader_epoch']))
            return revision

    def document(self, file_id, revision, exported_sha256, context):
        with self.owner.connection(readonly=True) as conn:
            value = row(conn, 'SELECT * FROM document_refs WHERE file_id=? AND revision=?', (file_id, revision))
            if value is None:
                raise Denied('ACCESS_REQUIRED')
            self._auth(conn, context, value['department'], 'DOCUMENT_READ')
            if (value['exported_sha256'] != exported_sha256 or
                    context.principal_id not in set(parse_json(value['reader_scope_json'])) | {value['owner_principal_id']}):
                raise Denied('ACCESS_REQUIRED')
            return value

    def claim_document(self, file_id, expected_revision, expected_generation, leader_epoch, context, *, lease_seconds=120):
        integer(expected_generation, minimum=0)
        integer(lease_seconds, minimum=1, maximum=600)
        with self.owner.write() as (conn, now):
            value = row(conn, '''SELECT h.*,r.department,r.owner_principal_id FROM document_heads h JOIN document_refs r
                ON r.file_id=h.file_id AND r.revision=h.revision WHERE h.file_id=?''', (file_id,))
            if value is None:
                raise Denied('ACCESS_REQUIRED')
            self._auth(conn, context, value['department'], 'DOCUMENT_WRITE')
            leader = row(conn, 'SELECT * FROM leader_state WHERE department=?', (value['department'],))
            if leader is None or leader['leader_epoch'] != leader_epoch or leader['delegation_paused']:
                raise Denied('STALE_ASSIGNMENT')
            if context.principal_id not in (value['owner_principal_id'], leader['principal_id']):
                raise Denied('ACCESS_REQUIRED')
            if value['revision'] != expected_revision or value['writer_generation'] != expected_generation:
                raise Denied('DOCUMENT_CONFLICT')
            if value['writer_token'] and value['writer_expires_at'] > now:
                raise Denied('RETRYABLE_BLOCKED')
            token = _fresh('doc_')
            self.store._changed(conn.execute('''UPDATE document_heads SET writer_principal_id=?,writer_generation=?,
                writer_token=?,writer_expires_at=?,leader_epoch=? WHERE file_id=? AND writer_generation=? AND revision=?''',
                (context.principal_id, expected_generation + 1, token, now + lease_seconds * 1000000,
                 leader_epoch, file_id, expected_generation, expected_revision)), 'DOCUMENT_CONFLICT')
            return dict(writer_token=token, writer_generation=expected_generation + 1, leader_epoch=leader_epoch)

    def start_response(self, response_id, worker_id, department, context):
        with self.owner.write() as (conn, now):
            self._auth(conn, context, department, 'RESPONSE')
            current = row(conn, 'SELECT * FROM response_attempts WHERE response_id=?', (response_id,))
            if current:
                if (current['worker_id'], current['principal_id'], current['department']) != (worker_id, context.principal_id, department):
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return current['status']
            conn.execute('''INSERT INTO response_attempts(response_id,worker_id,principal_id,department,started_at)
                VALUES(?,?,?,?,?)''', (response_id, worker_id, context.principal_id, department, now))
            return 'RUNNING'

    def control(self, response_id, action, expected_epoch, nonce, leader_epoch, context, *, owner_decision_id=None):
        if action not in ('STOP', 'DRAIN', 'CONTINUE', 'PAUSE') or not isinstance(nonce, str) or not 16 <= len(nonce) <= 180:
            raise ValueError('valid action and unique control nonce required')
        integer(expected_epoch, minimum=0)
        with self.owner.write() as (conn, now):
            response = row(conn, 'SELECT * FROM response_attempts WHERE response_id=?', (response_id,))
            if response is None:
                raise Denied('ACCESS_REQUIRED')
            scope = self._auth(conn, context, response['department'], 'CONTROL')
            prior = row(conn, 'SELECT * FROM response_controls WHERE nonce=?', (nonce,))
            if prior:
                if (prior['response_id'], prior['action'], prior['control_epoch'], prior['issuer_principal_id'], prior['leader_epoch'], prior['owner_decision_id']) != (
                        response_id, action, expected_epoch + 1, context.principal_id, leader_epoch, owner_decision_id):
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return prior['control_epoch']
            if response['status'] != 'RUNNING' or response['control_epoch'] != expected_epoch:
                raise Denied('STALE_ASSIGNMENT')
            if scope.owner:
                binding = dict(response_id=response_id, action=action, control_epoch=expected_epoch + 1, nonce=nonce)
                d = self._decision(conn, context, owner_decision_id, 'RESPONSE_CONTROL', binding, now)
                if d['authority_principal_id'] != context.principal_id:
                    raise Denied('OWNER_APPROVAL_REQUIRED')
            else:
                self._leader(conn, response['department'], context.principal_id, leader_epoch)
                if response['principal_id'] == context.principal_id:
                    raise Denied('ACCESS_REQUIRED')
                # Releasing a STOP/PAUSE requires owner approval, not the stopped worker.
                last = conn.execute('SELECT action FROM response_controls WHERE response_id=? ORDER BY control_epoch DESC LIMIT 1', (response_id,)).fetchone()
                if action == 'CONTINUE' and last and last[0] in ('STOP', 'PAUSE'):
                    self._decision(conn, context, owner_decision_id, 'RESPONSE_CONTROL',
                                   dict(response_id=response_id, action=action, control_epoch=expected_epoch + 1, nonce=nonce), now)
            conn.execute('INSERT INTO response_controls VALUES(?,?,?,?,?,?,?,?)',
                         (response_id, expected_epoch + 1, nonce, action, context.principal_id, leader_epoch, owner_decision_id, now))
            self.store._changed(conn.execute('UPDATE response_attempts SET control_epoch=? WHERE response_id=? AND control_epoch=?',
                                            (expected_epoch + 1, response_id, expected_epoch)))
            return expected_epoch + 1

    def observe_control(self, response_id, context):
        with self.owner.write() as (conn, now):
            response = row(conn, 'SELECT * FROM response_attempts WHERE response_id=?', (response_id,))
            if response is None or response['principal_id'] != context.principal_id:
                raise Denied('ACCESS_REQUIRED')
            self._auth(conn, context, response['department'], 'RESPONSE')
            # Observe the first unobserved STOP/DRAIN/PAUSE rather than skipping it
            # when several controls arrive between worker checkpoints.
            control = row(conn, '''SELECT * FROM response_controls WHERE response_id=? AND control_epoch>?
                ORDER BY control_epoch LIMIT 1''', (response_id, response['observed_control_epoch'] or 0))
            if control is not None and response['status'] == 'RUNNING':
                conn.execute('UPDATE response_attempts SET observed_control_epoch=? WHERE response_id=?', (control['control_epoch'], response_id))
                conn.execute('INSERT INTO response_control_observations VALUES(?,?,?,?) ON CONFLICT DO NOTHING',
                             (response_id, control['control_epoch'], context.principal_id, now))
            return control

    def next_eligible_claim(self, response_id, principal):
        from .selection import SQLiteClaimSelector
        return SQLiteClaimSelector(self).next(response_id, principal)

    def claim_for_response(self, response_id, command, context):
        # Store checks the control and records the attempt in the SAME claim TX.
        if command.get('command_kind') != 'CLAIM':
            raise ValueError('response admission requires a CLAIM command')
        return self.store.execute(command, context, response_id=response_id)

    def record_response_job(self, response_id, attempt_id, acceptance_ids, evidence, context):
        if not isinstance(acceptance_ids, list) or not acceptance_ids or not isinstance(evidence, dict):
            raise ValueError('acceptance IDs and exact reported evidence required')
        with self.owner.write() as (conn, now):
            response = row(conn, 'SELECT * FROM response_attempts WHERE response_id=?', (response_id,))
            attempt = row(conn, 'SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,))
            if response is None or attempt is None or response['principal_id'] != context.principal_id or response['status'] != 'RUNNING':
                raise Denied('ACCESS_REQUIRED')
            self._auth(conn, context, response['department'], 'RESPONSE')
            if attempt['principal_id'] != context.principal_id or attempt['worker_id'] != response['worker_id']:
                raise Denied('ACCESS_REQUIRED')
            if not conn.execute('SELECT 1 FROM response_job_starts WHERE response_id=? AND attempt_id=?',
                                (response_id, attempt_id)).fetchone():
                raise Denied('ACCESS_REQUIRED')
            outcome = attempt['status'] if attempt['status'] in ('CHECKPOINTED', 'SUBMITTED', 'YIELDED', 'STOPPED') else 'UNKNOWN'
            values = (response_id, attempt['job_id'], attempt_id, outcome, canonical(acceptance_ids), canonical(evidence))
            old = conn.execute('''SELECT response_id,job_id,attempt_id,outcome,acceptance_json,evidence_json
                FROM response_attempt_jobs WHERE response_id=? AND attempt_id=?''', (response_id, attempt_id)).fetchone()
            if old:
                if old != values:
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return outcome
            conn.execute('''INSERT INTO response_attempt_jobs(response_id,job_id,attempt_id,outcome,acceptance_json,evidence_json,created_at)
                VALUES(?,?,?,?,?,?,?)''', (*values, now))
            return outcome

    def finish_response(self, response_id, context, *, stop=False, resume_capsule=None, failures=()):
        with self.owner.write() as (conn, now):
            response = row(conn, 'SELECT * FROM response_attempts WHERE response_id=?', (response_id,))
            if response is None or response['principal_id'] != context.principal_id:
                raise Denied('ACCESS_REQUIRED')
            self._auth(conn, context, response['department'], 'RESPONSE')
            if response['status'] != 'RUNNING':
                return parse_json(response['report_json']) if response['report_json'] else None
            control = row(conn, 'SELECT * FROM response_controls WHERE response_id=? AND control_epoch=?',
                          (response_id, response['observed_control_epoch']))
            if stop and (control is None or control['action'] != 'STOP'):
                raise Denied('ACCESS_REQUIRED')
            if not stop and not isinstance(resume_capsule, dict):
                raise ValueError('resource-boundary YIELD requires an exact resume capsule')
            jobs = conn.execute('''SELECT s.job_id,s.attempt_id,COALESCE(j.outcome,'UNKNOWN'),
                COALESCE(j.acceptance_json,'[]'),COALESCE(j.evidence_json,'{}') FROM response_job_starts s
                LEFT JOIN response_attempt_jobs j ON j.response_id=s.response_id AND j.attempt_id=s.attempt_id
                WHERE s.response_id=? ORDER BY s.sequence''', (response_id,)).fetchall()
            card = dict(response_id=response_id, jobs=[dict(job_id=j[0], attempt_id=j[1], outcome=j[2],
                        acceptance_ids=parse_json(j[3]), reported_evidence=parse_json(j[4])) for j in jobs],
                        control_observed=dict(action=control['action'], epoch=control['control_epoch']) if control else None,
                        termination='STOP_OBSERVED' if stop else 'RESOURCE_BOUNDARY_YIELD',
                        failures=list(failures), independent_qa='NOT_ATTESTED')
            conn.execute('''UPDATE response_attempts SET status=?,ended_at=?,report_json=?,resume_json=?
                WHERE response_id=? AND status='RUNNING' ''',
                ('STOPPED' if stop else 'YIELDED', now, canonical(card), canonical(resume_capsule) if resume_capsule else None, response_id))
            return card
