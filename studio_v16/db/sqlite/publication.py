"""Transactional publication admission, dispatch evidence and terminal readback."""
from __future__ import annotations

import re
import sqlite3

from studio_v16.db.publication import PublicationDenied
from studio_v16.gateway.contracts import canonical_hash, validate, ContractError
from studio_v16.gateway.interfaces import State
from ..store import _fresh
from .connection import Unavailable
from .types import canonical, parse_json, epoch_us


def row(conn, sql, args=()):
    cursor = conn.execute(sql, args)
    value = cursor.fetchone()
    return dict(zip((c[0] for c in cursor.description), value)) if value is not None else None


def deny(code):
    raise PublicationDenied(code)


class SQLitePublication:
    def _validate_publication_state(self, conn, job_id, grant_id, state, now, *, intent=None):
        if not isinstance(state, State) or not state.grant_issuer_verified or not state.owner_approved:
            deny('ACCESS_REQUIRED')
        grant = validate(dict(state.grant), 'AUTH-V16/GRANT/1')
        if grant['job_id'] != job_id or grant['grant_id'] != grant_id:
            deny('ACCESS_REQUIRED')
        j = row(conn, 'SELECT * FROM jobs WHERE job_id=?', (job_id,))
        if j is None or j['state'] in ('BLOCKED', 'CANCELLED', 'DONE'):
            deny('ACCESS_REQUIRED')
        expected = (j['authority_revision'], j['approved_scope_hash'], j['source_repo'], j['source_base_sha'], j['assignment_epoch'])
        if ((grant['authority_revision'], grant['approved_scope_sha256'], grant['repo_id'],
             grant['source_base_sha'], grant['assignment_epoch']) != expected
                or (state.authority_revision, state.approved_scope_sha256, state.source_base_sha,
                    state.assignment_epoch) != (expected[0], expected[1], expected[3], expected[4]) or state.revoked):
            deny('AUTHORITY_REVISION_MISMATCH')
        g = row(conn, 'SELECT * FROM grants WHERE grant_id=? AND job_id=?', (grant_id, job_id))
        if g is None:
            deny('ACCESS_REQUIRED')
        d = row(conn, 'SELECT * FROM decisions WHERE decision_id=? AND job_id=?', (g['issuer_approval_ref'], job_id))
        if d is None:
            deny('ACCESS_REQUIRED')
        ids = (g['principal_id'], grant['issuer_principal_id'], d['authority_principal_id'])
        if len(set(ids)) != 3:
            deny('ACCESS_REQUIRED')
        for principal_id in ids:
            p = row(conn, 'SELECT status,kind FROM principals WHERE principal_id=?', (principal_id,))
            if p is None or p['status'] != 'ACTIVE' or p['kind'] == 'RECOVERY_READBACK':
                deny('IDENTITY_UNVERIFIED')
        if (g['revoked_at'] is not None or d['revoked_at'] is not None or grant['revoked_at'] is not None):
            deny('REVOKED_GRANT')
        if (g['expires_at'] <= now or (d['expires_at'] is not None and d['expires_at'] <= now)
                or epoch_us(grant['issued_at']) > now or epoch_us(grant['expires_at']) <= now):
            deny('EXPIRED_GRANT')
        if (g['principal_id'] != grant['principal_id'] or g['worker_id'] != grant['worker_id']
                or g['repo_id'] != j['source_repo'] or g['authority_revision'] != j['authority_revision']
                or g['approved_scope_hash'] != j['approved_scope_hash'] or g['source_base_sha'] != j['source_base_sha']
                or g['assignment_epoch'] != j['assignment_epoch'] or g['issuer_approval_ref'] != grant['owner_decision_ref']
                or set(parse_json(g['path_scope'])) != set(grant['owned_path_prefixes'])
                or g['policy_version'] != state.policy_revision or g['policy_version'] != grant['policy_revision']
                or d['authority_revision'] != j['authority_revision'] or d['approved_scope_hash'] != j['approved_scope_hash']):
            deny('INVALID_SCOPE')
        operation = g['operation']
        if operation not in grant['allowed_operations'] or d['action'] != operation:
            deny('ACCESS_REQUIRED')
        if operation not in ('SOURCE_BRANCH_UPDATE', 'DRIVE_REVISION_UPDATE'):
            deny('INVALID_SCOPE')
        if not conn.execute('''SELECT 1 FROM worker_assignments WHERE job_id=? AND worker_id=?
            AND assignment_epoch=? AND status='ACTIVE' AND revoked_at IS NULL''',
            (job_id, g['worker_id'], j['assignment_epoch'])).fetchone():
            deny('STALE_ASSIGNMENT')
        if not state.budget_authorized or conn.execute('''SELECT 1 FROM budget_reservations
            WHERE job_id=? AND amount>0 AND status<>'APPROVED' LIMIT 1''', (job_id,)).fetchone():
            deny('BUDGET_DENIED')
        a = row(conn, 'SELECT * FROM attempts WHERE attempt_id=? AND job_id=?', (state.holder_attempt_id, job_id))
        lease = row(conn, 'SELECT * FROM resource_leases WHERE repo_id=? AND resource_key=?',
                    (j['source_repo'], j['resource_key']))
        if (a is None or lease is None or a['worker_id'] != g['worker_id'] or a['principal_id'] != g['principal_id']
                or a['assignment_epoch'] != j['assignment_epoch'] or a['status'] in ('STOPPED', 'SUBMITTED')
                or a['lease_generation'] != state.lease_generation or a['source_base_sha'] != j['source_base_sha']
                or lease['holder_attempt_id'] != state.holder_attempt_id or lease['lease_generation'] != state.lease_generation
                or lease['expires_at'] <= now or lease['baseline_sha'] != j['source_base_sha']
                or grant['lease_generation'] != state.lease_generation):
            deny('STALE_LEASE')
        revision = f"sqlite:{j['version']}:{grant_id}:{j['assignment_epoch']}:{state.lease_generation}"
        if state.state_revision != revision:
            deny('AUTHORITY_REVISION_MISMATCH')
        if intent is None:
            return j
        target = intent['target']
        comparisons = {'principal_id': g['principal_id'], 'worker_id': g['worker_id'], 'grant_id': grant_id,
                       'owner_decision_ref': g['issuer_approval_ref'], 'approved_scope_sha256': j['approved_scope_hash'],
                       'authority_revision': j['authority_revision'], 'policy_revision': g['policy_version'],
                       'assignment_epoch': j['assignment_epoch'], 'lease_generation': state.lease_generation,
                       'attempt_id': state.holder_attempt_id, 'evidence_sha256': state.evidence_sha256}
        if any(intent.get(k) != value for k, value in comparisons.items()) or target['operation'] != operation:
            deny('INVALID_SCOPE')
        if operation == 'SOURCE_BRANCH_UPDATE':
            if (not state.source_writes_enabled or target['provider'] != 'GITHUB'
                    or target['repo_id'] != j['source_repo'] or target['source_base_sha'] != j['source_base_sha']
                    or target['work_branch'] != state.work_branch
                    or not target['work_branch'].startswith(f'studio/workorders/{job_id}/')
                    or target['patch_sha256'] != intent['artifact_sha256']):
                deny('INVALID_SCOPE')
        elif (not state.document_writes_enabled or target['provider'] != 'GOOGLE_DRIVE'
                or target['file_id'] != state.drive_file_id or target['expected_revision'] != state.drive_expected_revision
                or target['document_owner_ref'] != state.drive_document_owner_ref
                or target['exported_sha256'] != intent['artifact_sha256']):
            deny('INVALID_SCOPE')
        if operation == 'DRIVE_REVISION_UPDATE':
            document = row(conn, '''SELECT h.*,r.owner_principal_id,r.department,r.reader_scope_json
                FROM document_heads h JOIN document_refs r ON r.file_id=h.file_id AND r.revision=h.revision
                WHERE h.file_id=?''', (target['file_id'],))
            leader = row(conn, 'SELECT * FROM leader_state WHERE department=?', (document['department'],)) if document else None
            if (document is None or leader is None or document['revision'] != target['expected_revision']
                    or document['writer_principal_id'] != g['principal_id']
                    or document['writer_generation'] != state.document_writer_generation
                    or document['writer_token'] != state.document_writer_token or not state.document_writer_token
                    or document['writer_expires_at'] is None or document['writer_expires_at'] <= now
                    or document['leader_epoch'] != state.leader_epoch or leader['leader_epoch'] != state.leader_epoch
                    or leader['delegation_paused']):
                deny('DOCUMENT_CONFLICT')
        qa = state.qa
        if (qa is None or qa.verdict != 'PASS' or not state.evidence_sha256
                or qa.reviewer_principal_id in ids or qa.source_sha != j['source_base_sha']
                or any(intent['independent_qa'].get(k) != getattr(qa, k) for k in
                       ('review_id', 'reviewer_principal_id', 'subject_sha256', 'source_sha', 'evidence_ref', 'verdict'))):
            deny('QA_PENDING')
        review = row(conn, '''SELECT r.*,p.status AS reviewer_status,p.kind AS reviewer_kind FROM reviews r
            JOIN principals p ON p.principal_id=r.reviewer_principal_id WHERE r.review_id=? AND r.subject_publication_id=?''',
            (qa.review_id, intent['publication_id']))
        if (review is None or review['reviewer_principal_id'] != qa.reviewer_principal_id
                or review['subject_sha'] != qa.subject_sha256 or review['verdict'] != 'PASS'
                or review['evidence_ref'] != qa.evidence_ref or review['reviewer_status'] != 'ACTIVE'
                or review['reviewer_kind'] == 'RECOVERY_READBACK'
                or review['subject_sha'] not in {intent['artifact_sha256'], target.get('proposed_tree_sha256'), target.get('exported_sha256')}):
            deny('QA_PENDING')
        return j

    def verify_gateway_state(self, job_id, grant_id, state):
        with self.owner.write() as (conn, now):
            self._validate_publication_state(conn, job_id, grant_id, state, now)

    @staticmethod
    def _active(conn):
        if conn.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone() != ('ACTIVE',):
            deny('CUTOVER_NOT_APPROVED')

    def reserve_gateway_intent(self, intent, principal_id, state, state_revision, payload_sha256):
        try:
            validate(intent, 'PUBLISH-V16/INTENT/1')
            if (state.state_revision != state_revision or canonical_hash(intent) != payload_sha256
                    or intent['principal_id'] != principal_id or state.grant['principal_id'] != principal_id):
                deny('INVALID_SCOPE')
            with self.owner.write() as (conn, now):
                j = self._validate_publication_state(conn, intent['job_id'], intent['grant_id'], state, now, intent=intent)
                self._active(conn)
                existing = conn.execute('''SELECT publication_id,idempotency_key,intent_sha256 FROM publication_intents
                    WHERE publication_id=? OR idempotency_key=?''', (intent['publication_id'], intent['idempotency_key'])).fetchall()
                if existing:
                    if len(existing) == 1 and existing[0] == (intent['publication_id'], intent['idempotency_key'], payload_sha256):
                        return 'DUPLICATE'
                    deny('IDEMPOTENCY_CONFLICT')
                if conn.execute('''SELECT 1 FROM publication_keys WHERE publication_id=? OR idempotency_key=?
                    OR (job_id=? AND artifact_sha256=?)''', (intent['publication_id'], intent['idempotency_key'],
                                                           intent['job_id'], intent['artifact_sha256'])).fetchone():
                    deny('IDEMPOTENCY_CONFLICT')
                target = intent['target']
                provider = target['provider']
                conn.execute('INSERT INTO publication_keys VALUES(?,?,?,?)',
                             (intent['publication_id'], intent['idempotency_key'], intent['job_id'], intent['artifact_sha256']))
                if provider == 'GITHUB':
                    conn.execute('''INSERT INTO source_publications(publication_id,job_id,attempt_id,repo_id,work_branch,
                        baseline_sha,patch_sha256,proposed_tree_hash,idempotency_key,status) VALUES(?,?,?,?,?,?,?,?,?,'PREPARED')''',
                        (intent['publication_id'], intent['job_id'], intent['attempt_id'], target['repo_id'], target['work_branch'],
                         j['source_base_sha'], target['patch_sha256'], target['proposed_tree_sha256'], intent['idempotency_key']))
                else:
                    conn.execute('''INSERT INTO document_publications(publication_id,job_id,attempt_id,drive_file_id,
                        expected_revision,exported_sha256,idempotency_key,status) VALUES(?,?,?,?,?,?,?,'PREPARED')''',
                        (intent['publication_id'], intent['job_id'], intent['attempt_id'], target['file_id'], target['expected_revision'],
                         target['exported_sha256'], intent['idempotency_key']))
                event = self._journal_internal(conn, dict(command_id='pub:' + intent['publication_id'],
                    command_kind='RESERVE_PUBLICATION', job_id=intent['job_id'], worker_id=intent['worker_id'],
                    attempt_id=intent['attempt_id'], assignment_epoch=intent['assignment_epoch'], payload=intent), now)
                out = _fresh('out_')
                conn.execute('''INSERT INTO outbox(outbox_id,event_id,kind,destination,status,payload_sha256,next_retry_at,created_at)
                    VALUES(?,?,?,?,'PREPARED',?,?,?)''', (out, event, 'GITHUB_EFFECT' if provider == 'GITHUB' else 'DRIVE_EFFECT',
                    target['work_branch'] if provider == 'GITHUB' else target['file_id'], payload_sha256, now, now))
                conn.execute('''INSERT INTO publication_intents(publication_id,idempotency_key,job_id,grant_id,principal_id,
                    state_revision,authority_revision,policy_revision,assignment_epoch,lease_generation,owner_decision_ref,
                    qa_review_id,evidence_sha256,intent_sha256,intent_json,provider,patch_sha256,proposed_tree_sha256,
                    expected_revision,outbox_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (intent['publication_id'], intent['idempotency_key'], intent['job_id'], intent['grant_id'], principal_id,
                     state_revision, intent['authority_revision'], intent['policy_revision'], intent['assignment_epoch'],
                     intent['lease_generation'], intent['owner_decision_ref'], intent['independent_qa']['review_id'],
                     intent['evidence_sha256'], payload_sha256, canonical(intent), provider, target.get('patch_sha256'),
                     target.get('proposed_tree_sha256'), target.get('expected_revision'), out, now))
            return 'COMMITTED'
        except PublicationDenied as exc:
            return exc.code
        except ContractError as exc:
            return exc.code
        except sqlite3.IntegrityError:
            return 'IDEMPOTENCY_CONFLICT'
        except (Unavailable, sqlite3.DatabaseError):
            return 'GATEWAY_UNAVAILABLE'

    def begin_effect_dispatch(self, intent, state):
        validate(intent, 'PUBLISH-V16/INTENT/1')
        with self.owner.write() as (conn, now):
            self._validate_publication_state(conn, intent['job_id'], intent['grant_id'], state, now, intent=intent)
            self._active(conn)
            record = self._intent(conn, intent['publication_id'], intent['idempotency_key'])
            if (record is None or record['intent_sha256'] != canonical_hash(intent)
                    or record['status'] != 'PREPARED' or record['state_revision'] != state.state_revision):
                deny('IDEMPOTENCY_CONFLICT')
            previous = row(conn, '''SELECT dispatch_id FROM effect_dispatches WHERE publication_id=?
                ORDER BY dispatch_sequence DESC LIMIT 1''', (intent['publication_id'],))
            if previous and not conn.execute('SELECT 1 FROM effect_absence_proofs WHERE dispatch_id=?',
                                             (previous['dispatch_id'],)).fetchone():
                deny('RETRYABLE_BLOCKED')
            dispatch = _fresh('dispatch_')
            conn.execute('''INSERT INTO effect_dispatches(dispatch_id,publication_id,intent_sha256,principal_id,
                assignment_epoch,lease_generation,policy_revision,provider_operation_id,dispatch_started_at,previous_dispatch_id)
                VALUES(?,?,?,?,?,?,?,?,?,?)''', (dispatch, intent['publication_id'], record['intent_sha256'], intent['principal_id'],
                intent['assignment_epoch'], intent['lease_generation'], intent['policy_revision'], dispatch, now,
                previous['dispatch_id'] if previous else None))
            changed = conn.execute("UPDATE outbox SET status='IN_FLIGHT',attempts=attempts+1 WHERE outbox_id=? AND status IN ('PREPARED','RECONCILE_REQUIRED') AND attempts<5",
                                   (record['outbox_id'],))
            if changed.rowcount != 1:
                deny('RETRYABLE_BLOCKED')
        return dispatch

    def authorize_gateway_effect(self, intent, state):
        try:
            return bool(self.begin_effect_dispatch(intent, state))
        except (PublicationDenied, ContractError, Unavailable, sqlite3.DatabaseError):
            return False

    @staticmethod
    def _intent(conn, publication_id, idempotency_key):
        value = row(conn, 'SELECT * FROM publication_intents WHERE publication_id=? AND idempotency_key=?',
                    (publication_id, idempotency_key))
        if value is None:
            return None
        intent = parse_json(value['intent_json'])
        validate(intent, 'PUBLISH-V16/INTENT/1')
        if (canonical_hash(intent) != value['intent_sha256'] or intent['publication_id'] != publication_id
                or intent['idempotency_key'] != idempotency_key):
            deny('IDEMPOTENCY_CONFLICT')
        value['intent'] = intent
        return value

    def recorded_gateway_intent(self, publication_id, idempotency_key):
        with self.owner.connection(readonly=True) as conn:
            record = self._intent(conn, publication_id, idempotency_key)
            return record['intent'] if record else None

    @staticmethod
    def _exact_observation(intent, receipt):
        target, observed = intent['target'], receipt['observed']
        if receipt['provider'] != target['provider'] or not isinstance(observed, dict):
            return False
        if target['provider'] == 'GITHUB':
            expected = dict(repo_id=target['repo_id'], work_branch=target['work_branch'],
                            tree_sha256=target['proposed_tree_sha256'], artifact_sha256=target['patch_sha256'])
            return (set(observed) == set(expected) | {'commit_sha'}
                    and all(observed[k] == v for k, v in expected.items())
                    and isinstance(observed['commit_sha'], str)
                    and re.fullmatch('[0-9a-f]{40}', observed['commit_sha']) is not None)
        expected = dict(file_id=target['file_id'], exported_sha256=target['exported_sha256'])
        return (set(observed) == set(expected) | {'revision'}
                and all(observed[k] == v for k, v in expected.items())
                and isinstance(observed['revision'], str) and bool(observed['revision'])
                and observed['revision'] != target['expected_revision'])

    def _apply_receipt(self, conn, record, receipt, now):
        validate(receipt, 'PUBLISH-V16/READBACK/1')
        if (receipt['publication_id'] != record['publication_id'] or receipt['idempotency_key'] != record['idempotency_key']
                or receipt['provider'] != record['provider']):
            deny('INVALID_SCOPE')
        intent = record['intent']
        status = receipt['status']
        if status == 'VERIFIED' and not self._exact_observation(intent, receipt):
            deny('SOURCE_SHA_MISMATCH')
        if record['status'] == 'VERIFIED':
            saved = parse_json(record['verified_receipt_json'])
            if status != 'VERIFIED' or saved['observed'] != receipt['observed']:
                deny('IDEMPOTENCY_CONFLICT')
            if saved.get('provider_request_id') and receipt.get('provider_request_id') and saved['provider_request_id'] != receipt['provider_request_id']:
                deny('IDEMPOTENCY_CONFLICT')
            return 'VERIFIED'
        if record['status'] in ('CONFLICT', 'BLOCKED'):
            deny('IDEMPOTENCY_CONFLICT')
        dispatch = row(conn, '''SELECT * FROM effect_dispatches WHERE publication_id=?
            ORDER BY dispatch_sequence DESC LIMIT 1''', (record['publication_id'],))
        if (dispatch is None or dispatch['intent_sha256'] != record['intent_sha256']
                or dispatch['principal_id'] != record['principal_id'] or dispatch['assignment_epoch'] != record['assignment_epoch']
                or dispatch['lease_generation'] != record['lease_generation'] or dispatch['policy_revision'] != record['policy_revision']):
            deny('ACCESS_REQUIRED')
        if status == 'VERIFIED':
            observed = receipt['observed']
            if record['provider'] == 'GITHUB':
                revision = observed['commit_sha']
                conn.execute('''UPDATE source_publications SET status='VERIFIED',result_commit_sha=?,
                    observed_sha256=?,provider_request_id=?,verified_at=? WHERE publication_id=?''',
                    (revision, observed['tree_sha256'], receipt['provider_request_id'], now, record['publication_id']))
            else:
                revision = observed['revision']
                document = row(conn, '''SELECT r.* FROM document_heads h JOIN document_refs r
                    ON r.file_id=h.file_id AND r.revision=h.revision WHERE h.file_id=?''', (intent['target']['file_id'],))
                if document is None or document['revision'] != intent['target']['expected_revision']:
                    deny('DOCUMENT_CONFLICT')
                conn.execute('''INSERT INTO document_refs(file_id,revision,exported_sha256,owner_principal_id,
                    department,reader_scope_json,source_ref,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                    (document['file_id'], revision, observed['exported_sha256'], document['owner_principal_id'],
                     document['department'], document['reader_scope_json'], 'publication:' + record['publication_id'], now))
                conn.execute('''UPDATE document_heads SET revision=?,writer_principal_id=NULL,
                    writer_token=NULL,writer_expires_at=NULL WHERE file_id=? AND revision=?''',
                    (revision, document['file_id'], document['revision']))
                conn.execute('''UPDATE document_publications SET status='VERIFIED',resulting_revision=?,
                    observed_sha256=?,provider_request_id=?,verified_at=? WHERE publication_id=?''',
                    (revision, observed['exported_sha256'], receipt['provider_request_id'], now, record['publication_id']))
            conn.execute("UPDATE publication_intents SET status='VERIFIED',verified_receipt_json=?,verified_at=? WHERE publication_id=?",
                         (canonical(receipt), now, record['publication_id']))
            conn.execute('''UPDATE outbox SET status='DELIVERED',provider_ref=?,provider_effect_sha256=?,
                claim_token=NULL,claim_expires_at=NULL WHERE outbox_id=?''', (revision, record['intent_sha256'], record['outbox_id']))
            return 'VERIFIED'
        if status == 'CONFLICT':
            table = 'source_publications' if record['provider'] == 'GITHUB' else 'document_publications'
            conn.execute(f"UPDATE {table} SET status='CONFLICT' WHERE publication_id=?", (record['publication_id'],))
            conn.execute("UPDATE publication_intents SET status='CONFLICT',conflict_receipt_json=? WHERE publication_id=?",
                         (canonical(receipt), record['publication_id']))
            conn.execute("UPDATE outbox SET status='BLOCKED',dead_letter_reason='PROVIDER_READBACK_CONFLICT' WHERE outbox_id=?",
                         (record['outbox_id'],))
            return 'CONFLICT'
        if status != 'EXTERNAL_OBSERVED':
            deny('INVALID_SCOPE')
        conn.execute("UPDATE outbox SET status='RECONCILE_REQUIRED' WHERE outbox_id=? AND status='IN_FLIGHT'", (record['outbox_id'],))
        return 'RECONCILE_REQUIRED'

    def _worker_reconcile(self, publication_id, idempotency_key, state, state_revision, receipt, expected_status):
        try:
            if state.state_revision != state_revision or receipt['status'] != expected_status:
                return False
            with self.owner.write() as (conn, now):
                record = self._intent(conn, publication_id, idempotency_key)
                if record is None or record['state_revision'] != state_revision:
                    return False
                self._validate_publication_state(conn, record['job_id'], record['grant_id'], state, now, intent=record['intent'])
                self._active(conn)
                return self._apply_receipt(conn, record, receipt, now) == expected_status
        except (PublicationDenied, ContractError, Unavailable, sqlite3.DatabaseError):
            return False

    def reconcile_gateway_intent(self, publication_id, idempotency_key, state, state_revision, receipt):
        return self._worker_reconcile(publication_id, idempotency_key, state, state_revision, receipt, 'VERIFIED')

    def mark_gateway_conflict(self, publication_id, idempotency_key, state, state_revision, receipt):
        return self._worker_reconcile(publication_id, idempotency_key, state, state_revision, receipt, 'CONFLICT')
