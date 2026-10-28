"""Atomic WG-D publication authority implementation for WG-C PostgreSQL.

Trusted issuer/owner/QA evidence comes from an isolated gateway-side authority
service; it is never manufactured from worker command fields. The database is
still the transactionally checked source of current revocation and fencing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from studio_v16.gateway.contracts import canonical_hash, validate
from studio_v16.gateway.interfaces import State
from .store_helpers import canonical_json


class PublicationDenied(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _json(value):
    return value if isinstance(value, dict) else json.loads(value)


def _fresh_time(value):
    return value is not None and value > datetime.now(timezone.utc)


class PublicationAuthorityMixin:
    def _validate_publication_state(self, conn, job_id: str, grant_id: str, state: State,
                                    *, intent: dict | None = None) -> dict:
        """Lock live authority rows. Hold these locks through reserve/reconcile."""
        if not isinstance(state, State) or not state.grant_issuer_verified or not state.owner_approved:
            raise PublicationDenied('ACCESS_REQUIRED')
        grant = validate(dict(state.grant), 'AUTH-V16/GRANT/1')
        if grant['job_id'] != job_id or grant['grant_id'] != grant_id:
            raise PublicationDenied('ACCESS_REQUIRED')
        j = conn.execute('''SELECT state, authority_revision, approved_scope_hash, source_repo,
                           source_base_sha, resource_key, assignment_epoch, version
                           FROM nomad_v16.jobs WHERE job_id=%s FOR UPDATE''', (job_id,)).fetchone()
        if not j or j[0] in ('BLOCKED','CANCELLED','DONE'):
            raise PublicationDenied('ACCESS_REQUIRED')
        _, revision, scope, repo, base, resource, epoch, version = j
        if (revision != state.authority_revision or revision != grant['authority_revision'] or
            scope.strip() != grant['approved_scope_sha256'] or scope.strip() != state.approved_scope_sha256 or
            repo != grant['repo_id'] or base.strip() != grant['source_base_sha'] or
            base.strip() != state.source_base_sha or epoch != grant['assignment_epoch'] or
            epoch != state.assignment_epoch or state.revoked):
            raise PublicationDenied('AUTHORITY_REVISION_MISMATCH')
        active = conn.execute('''SELECT 1 FROM nomad_v16.worker_assignments WHERE job_id=%s
            AND worker_id=%s AND assignment_epoch=%s AND status='ACTIVE' AND revoked_at IS NULL''',
            (job_id, grant['worker_id'], epoch)).fetchone()
        if not active:
            raise PublicationDenied('STALE_ASSIGNMENT')
        principal = conn.execute('''SELECT status FROM nomad_v16.principals WHERE principal_id=%s FOR SHARE''',
                                 (grant['principal_id'],)).fetchone()
        if not principal or principal[0] != 'ACTIVE':
            raise PublicationDenied('IDENTITY_UNVERIFIED')
        record = conn.execute('''SELECT g.principal_id, g.worker_id, g.repo_id, g.path_scope, g.operation,
            g.policy_version, g.authority_revision, g.approved_scope_hash, g.source_base_sha,
            g.assignment_epoch, g.expires_at, g.revoked_at, g.issuer_approval_ref,
            d.action, d.authority_revision, d.approved_scope_hash, d.expires_at, d.revoked_at,
            owner.principal_id, owner.status, issuer.status
            FROM nomad_v16.grants g JOIN nomad_v16.decisions d ON d.decision_id=g.issuer_approval_ref
            JOIN nomad_v16.principals owner ON owner.principal_id=d.authority_principal_id
            JOIN nomad_v16.principals issuer ON issuer.principal_id=%s
            WHERE g.grant_id=%s AND g.job_id=%s AND d.job_id=g.job_id
            FOR UPDATE OF g, d FOR SHARE OF owner, issuer''', (grant['issuer_principal_id'], grant_id, job_id)).fetchone()
        if not record or record[19] != 'ACTIVE' or record[20] != 'ACTIVE':
            raise PublicationDenied('ACCESS_REQUIRED')
        (gp, gw, gr, paths, operation, policy, ga, gs, gb, ge, expiry, revoked,
         owner_ref, decision_op, decision_revision, decision_scope, decision_expiry,
         decision_revoked, owner_id, _, _) = record
        if len({gp, grant['issuer_principal_id'], owner_id}) != 3:
            raise PublicationDenied('ACCESS_REQUIRED')
        if revoked or decision_revoked or grant['revoked_at'] is not None:
            raise PublicationDenied('REVOKED_GRANT')
        issued=datetime.fromisoformat(grant['issued_at'].replace('Z','+00:00'))
        if issued > datetime.now(timezone.utc) or not _fresh_time(expiry) or (decision_expiry is not None and not _fresh_time(decision_expiry)):
            raise PublicationDenied('EXPIRED_GRANT')
        if (gp != grant['principal_id'] or gw != grant['worker_id'] or gr != repo or
            set(paths) != set(grant['owned_path_prefixes']) or
            policy != state.policy_revision or policy != grant['policy_revision'] or
            ga != revision or gs.strip() != scope.strip() or gb.strip() != base.strip() or
            ge != epoch or owner_ref != grant['owner_decision_ref'] or
            decision_revision != revision or decision_scope.strip() != scope.strip()):
            raise PublicationDenied('INVALID_SCOPE')
        if operation not in grant['allowed_operations'] or decision_op != operation:
            raise PublicationDenied('ACCESS_REQUIRED')
        if intent is not None:
            target = intent['target']
            if (intent['principal_id'] != gp or intent['worker_id'] != gw or
                intent['grant_id'] != grant_id or intent['owner_decision_ref'] != owner_ref or
                intent['approved_scope_sha256'] != scope.strip() or
                intent['authority_revision'] != revision or intent['policy_revision'] != policy or
                intent['assignment_epoch'] != epoch or intent['lease_generation'] != state.lease_generation or
                intent['evidence_sha256'] != state.evidence_sha256 or target['operation'] != operation or
                (operation == 'SOURCE_BRANCH_UPDATE' and not state.source_writes_enabled) or
                (operation == 'DRIVE_REVISION_UPDATE' and not state.document_writes_enabled)):
                raise PublicationDenied('INVALID_SCOPE')
        if not state.budget_authorized:
            raise PublicationDenied('BUDGET_DENIED')
        reservations=conn.execute('''SELECT amount,status FROM nomad_v16.budget_reservations
            WHERE job_id=%s FOR SHARE''', (job_id,)).fetchall()
        if any(amount>0 and status!='APPROVED' for amount,status in reservations):
            raise PublicationDenied('BUDGET_DENIED')
        attempt = conn.execute('''SELECT worker_id, principal_id, assignment_epoch, status,
            lease_generation, source_base_sha FROM nomad_v16.attempts
            WHERE attempt_id=%s AND job_id=%s FOR UPDATE''',
            (state.holder_attempt_id, job_id)).fetchone()
        if (not attempt or attempt[0] != gw or attempt[1] != gp or attempt[2] != epoch or
            attempt[3] in ('STOPPED','SUBMITTED') or attempt[4] != state.lease_generation or
            attempt[5].strip() != base.strip()):
            raise PublicationDenied('STALE_LEASE')
        lease = conn.execute('''SELECT holder_attempt_id, lease_generation, expires_at FROM
            nomad_v16.resource_leases WHERE repo_id=%s AND resource_key=%s FOR UPDATE''',
            (repo, resource)).fetchone()
        if (not lease or lease[0] != state.holder_attempt_id or
            lease[1] != state.lease_generation or not _fresh_time(lease[2])):
            raise PublicationDenied('STALE_LEASE')
        # Tie the evidence revision to the DB job version and current lease fence.
        expected_revision=f'pg:{version}:{grant_id}:{epoch}:{state.lease_generation}'
        if state.state_revision != expected_revision:
            raise PublicationDenied('AUTHORITY_REVISION_MISMATCH')
        if intent is not None:
            if (intent['attempt_id'] != state.holder_attempt_id or
                (operation == 'SOURCE_BRANCH_UPDATE' and
                  (target['repo_id'] != repo or target['source_base_sha'] != base.strip() or
                   target['work_branch'] != state.work_branch or
                   target['patch_sha256'] != intent['artifact_sha256'])) or
                (operation == 'DRIVE_REVISION_UPDATE' and
                  (target['file_id'] != state.drive_file_id or
                   target['document_owner_ref'] != state.drive_document_owner_ref or
                   target['expected_revision'] != state.drive_expected_revision or
                   target['exported_sha256'] != intent['artifact_sha256']))):
                raise PublicationDenied('INVALID_SCOPE')
            if operation == 'SOURCE_BRANCH_UPDATE':
                if target['work_branch'] in ('main','master','develop','development') or not target['work_branch'].startswith(f'studio/workorders/{job_id}/'):
                    raise PublicationDenied('INVALID_SCOPE')
            if (not state.qa or state.qa.verdict != 'PASS' or
                state.qa.reviewer_principal_id == gp or
                any(intent['independent_qa'][k] != getattr(state.qa, k) for k in
                    ('review_id','reviewer_principal_id','subject_sha256','source_sha','evidence_ref','verdict')) or
                state.qa.source_sha != base.strip()):
                raise PublicationDenied('QA_PENDING')
            review = conn.execute('''SELECT r.reviewer_principal_id, r.subject_sha, r.verdict,
                r.evidence_ref, p.status FROM nomad_v16.reviews r JOIN nomad_v16.principals p
                ON p.principal_id=r.reviewer_principal_id WHERE r.review_id=%s
                AND r.subject_publication_id=%s FOR SHARE OF p''',
                (state.qa.review_id, intent['publication_id'])).fetchone()
            targets = {intent['artifact_sha256'], target.get('proposed_tree_sha256'), target.get('exported_sha256')}
            if (not review or review[0] != state.qa.reviewer_principal_id or
                review[0] in (gp,owner_id,grant['issuer_principal_id']) or
                review[1].strip() not in targets or review[1].strip() != state.qa.subject_sha256 or
                review[2] != 'PASS' or review[3] != state.qa.evidence_ref or review[4] != 'ACTIVE'):
                raise PublicationDenied('QA_PENDING')
        return {'job_id': job_id, 'repo': repo, 'base': base.strip(), 'epoch': epoch,
                'grant_id': grant_id, 'operation': operation, 'version': version}

    def verify_gateway_state(self, job_id: str, grant_id: str, state: State) -> None:
        with self._connect() as conn, conn.transaction():
            self._validate_publication_state(conn, job_id, grant_id, state)

    def reserve_gateway_intent(self, intent: dict, principal_id: str, state: State,
                               state_revision: str, payload_sha256: str) -> str:
        validate(intent, 'PUBLISH-V16/INTENT/1')
        if (state.state_revision != state_revision or
            canonical_hash(intent) != payload_sha256 or
            intent['principal_id'] != principal_id or
            state.grant['principal_id'] != principal_id):
            return 'INVALID_SCOPE'
        try:
            with self._connect() as conn, conn.transaction():
                auth = self._validate_publication_state(conn, intent['job_id'], intent['grant_id'], state, intent=intent)
                mode = conn.execute('SELECT mode FROM nomad_v16.authority_mode WHERE singleton=true FOR UPDATE').fetchone()
                if not mode or mode[0] != 'ACTIVE':
                    raise PublicationDenied('CUTOVER_NOT_APPROVED')
                existing = conn.execute('''SELECT publication_id, idempotency_key, intent_sha256
                    FROM nomad_v16.publication_intents WHERE publication_id=%s OR idempotency_key=%s FOR UPDATE''',
                    (intent['publication_id'], intent['idempotency_key'])).fetchone()
                if existing:
                    if (existing[0] == intent['publication_id'] and
                        existing[1].strip() == intent['idempotency_key'] and
                        existing[2].strip() == payload_sha256):
                        return 'DUPLICATE'
                    raise PublicationDenied('IDEMPOTENCY_CONFLICT')
                legacy = conn.execute('''SELECT 1 FROM nomad_v16.publication_keys WHERE publication_id=%s
                    OR idempotency_key=%s OR (job_id=%s AND artifact_sha256=%s)''',
                    (intent['publication_id'],intent['idempotency_key'],intent['job_id'],intent['artifact_sha256'])).fetchone()
                if legacy:
                    raise PublicationDenied('IDEMPOTENCY_CONFLICT')
                target=intent['target']; provider=target['provider']
                # The full intent, internal identities, and publication effect queue are ONE transaction.
                inserted=conn.execute('''INSERT INTO nomad_v16.publication_keys(publication_id,idempotency_key,job_id,artifact_sha256)
                    VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING publication_id''',
                    (intent['publication_id'],intent['idempotency_key'],intent['job_id'],intent['artifact_sha256'])).fetchone()
                if not inserted:
                    raise PublicationDenied('IDEMPOTENCY_CONFLICT')
                if provider == 'GITHUB':
                    conn.execute('''INSERT INTO nomad_v16.source_publications
                        (publication_id,job_id,attempt_id,repo_id,work_branch,baseline_sha,patch_sha256,
                         proposed_tree_hash,idempotency_key,status)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'PREPARED')''',
                        (intent['publication_id'],intent['job_id'],intent['attempt_id'],target['repo_id'],
                         target['work_branch'],auth['base'],target['patch_sha256'],
                         target['proposed_tree_sha256'],intent['idempotency_key']))
                else:
                    conn.execute('''INSERT INTO nomad_v16.document_publications
                        (publication_id,job_id,attempt_id,drive_file_id,expected_revision,
                         exported_sha256,idempotency_key,status)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,'PREPARED')''',
                        (intent['publication_id'],intent['job_id'],intent['attempt_id'],target['file_id'],
                         target['expected_revision'],target['exported_sha256'],intent['idempotency_key']))
                event_id=self._journal_internal(conn, {'command_id':'pub:'+intent['publication_id'],
                    'command_kind':'RESERVE_PUBLICATION','job_id':intent['job_id'],'worker_id':intent['worker_id'],
                    'attempt_id':intent['attempt_id'],'assignment_epoch':intent['assignment_epoch'],'payload':intent})
                out_id='out_'+__import__('uuid').uuid4().hex
                conn.execute('''INSERT INTO nomad_v16.outbox(outbox_id,event_id,kind,destination,status,payload_sha256)
                    VALUES(%s,%s,%s,%s,'PREPARED',%s)''',
                    (out_id,event_id,'GITHUB_EFFECT' if provider=='GITHUB' else 'DRIVE_EFFECT',
                     target['work_branch'] if provider=='GITHUB' else target['file_id'],payload_sha256))
                conn.execute('''INSERT INTO nomad_v16.publication_intents
                    (publication_id,idempotency_key,job_id,grant_id,principal_id,state_revision,
                     authority_revision,policy_revision,assignment_epoch,lease_generation,
                     owner_decision_ref,qa_review_id,evidence_sha256,intent_sha256,intent_json,
                     provider,patch_sha256,proposed_tree_sha256,expected_revision,outbox_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)''',
                    (intent['publication_id'],intent['idempotency_key'],intent['job_id'],intent['grant_id'],
                     principal_id,state_revision,intent['authority_revision'],intent['policy_revision'],
                     intent['assignment_epoch'],intent['lease_generation'],intent['owner_decision_ref'],
                     intent['independent_qa']['review_id'],intent['evidence_sha256'],payload_sha256,
                     canonical_json(intent),provider,target.get('patch_sha256'),target.get('proposed_tree_sha256'),
                     target.get('expected_revision'),out_id))
            return 'COMMITTED'
        except PublicationDenied as exc:
            return exc.code

    def authorize_gateway_effect(self, intent: dict, state: State) -> bool:
        """Recheck pending intent immediately before the external provider CAS.

        Lock order: job/grant/attempt/lease -> mode -> publication row.
        """
        try:
            validate(intent,'PUBLISH-V16/INTENT/1')
            with self._connect() as conn,conn.transaction():
                self._validate_publication_state(conn,intent['job_id'],intent['grant_id'],state,intent=intent)
                mode=conn.execute('SELECT mode FROM nomad_v16.authority_mode WHERE singleton=true FOR UPDATE').fetchone()
                if not mode or mode[0]!='ACTIVE':
                    return False
                row=conn.execute('''SELECT intent_sha256,status,state_revision FROM
                    nomad_v16.publication_intents WHERE publication_id=%s AND idempotency_key=%s FOR UPDATE''',
                    (intent['publication_id'],intent['idempotency_key'])).fetchone()
                return bool(row and row[0].strip()==canonical_hash(intent) and
                            row[1]=='PREPARED' and row[2]==state.state_revision)
        except PublicationDenied:
            return False

    def recorded_gateway_intent(self, publication_id: str, idempotency_key: str) -> dict | None:
        with self._connect() as conn, conn.transaction():
            row=conn.execute('''SELECT intent_json,intent_sha256 FROM nomad_v16.publication_intents
                WHERE publication_id=%s AND idempotency_key=%s''', (publication_id,idempotency_key)).fetchone()
            if not row:
                return None
            intent=_json(row[0])
            validate(intent,'PUBLISH-V16/INTENT/1')
            if (canonical_hash(intent) != row[1].strip() or
                intent['publication_id'] != publication_id or intent['idempotency_key'] != idempotency_key):
                return None
            return intent

    def mark_gateway_conflict(self, publication_id: str, idempotency_key: str,
                              state: State, state_revision: str, receipt: dict) -> bool:
        """Persist a gateway-verified negative readback and fence future retries."""
        validate(receipt,'PUBLISH-V16/READBACK/1')
        if (receipt['publication_id'] != publication_id or receipt['idempotency_key'] != idempotency_key or
            receipt['status'] != 'CONFLICT' or state.state_revision != state_revision):
            return False
        try:
            with self._connect() as conn, conn.transaction():
                initial=conn.execute('''SELECT intent_json,intent_sha256,grant_id FROM
                   nomad_v16.publication_intents WHERE publication_id=%s AND idempotency_key=%s''',
                   (publication_id,idempotency_key)).fetchone()
                if not initial:
                    return False
                intent=_json(initial[0])
                if canonical_hash(intent)!=initial[1].strip():
                    return False
                self._validate_publication_state(conn,intent['job_id'],initial[2],state,intent=intent)
                mode=conn.execute('SELECT mode FROM nomad_v16.authority_mode WHERE singleton=true FOR UPDATE').fetchone()
                if not mode or mode[0]!='ACTIVE':
                    return False
                row=conn.execute('''SELECT intent_json,intent_sha256,grant_id,status,outbox_id FROM
                   nomad_v16.publication_intents WHERE publication_id=%s AND idempotency_key=%s FOR UPDATE''',
                   (publication_id,idempotency_key)).fetchone()
                if not row or row[3]!='PREPARED' or row[1].strip()!=initial[1].strip():
                    return False
                target=intent['target']; table='source_publications' if target['provider']=='GITHUB' else 'document_publications'
                if receipt['provider']!=target['provider']:
                    return False
                conn.execute(f"UPDATE nomad_v16.{table} SET status='CONFLICT' WHERE publication_id=%s",(publication_id,))
                conn.execute("UPDATE nomad_v16.publication_intents SET status='CONFLICT',conflict_receipt_json=%s::jsonb WHERE publication_id=%s",(canonical_json(receipt),publication_id))
                conn.execute("UPDATE nomad_v16.outbox SET status='BLOCKED',dead_letter_reason='PROVIDER_READBACK_CONFLICT' WHERE outbox_id=%s",(row[4],))
                return True
        except PublicationDenied:
            return False

    def reconcile_gateway_intent(self, publication_id: str, idempotency_key: str,
                                 state: State, state_revision: str, receipt: dict) -> bool:
        validate(receipt,'PUBLISH-V16/READBACK/1')
        if (receipt['publication_id'] != publication_id or receipt['idempotency_key'] != idempotency_key or
            receipt['status'] != 'VERIFIED' or receipt['observed'] is None or
            state.state_revision != state_revision):
            return False
        try:
            with self._connect() as conn, conn.transaction():
                initial=conn.execute('''SELECT intent_json,intent_sha256,grant_id FROM
                    nomad_v16.publication_intents WHERE publication_id=%s AND idempotency_key=%s''',
                    (publication_id,idempotency_key)).fetchone()
                if not initial:
                    return False
                intent=_json(initial[0]); target=intent['target']; provider=target['provider']
                if canonical_hash(intent) != initial[1].strip():
                    return False
                self._validate_publication_state(conn,intent['job_id'],initial[2],state,intent=intent)
                mode=conn.execute('SELECT mode FROM nomad_v16.authority_mode WHERE singleton=true FOR UPDATE').fetchone()
                if not mode or mode[0]!='ACTIVE':
                    return False
                row=conn.execute('''SELECT intent_json,intent_sha256,grant_id,principal_id,state_revision,
                    status,outbox_id FROM nomad_v16.publication_intents
                    WHERE publication_id=%s AND idempotency_key=%s FOR UPDATE''',
                    (publication_id,idempotency_key)).fetchone()
                if not row or row[4]!=state_revision or row[1].strip()!=initial[1].strip():
                    return False
                observed=receipt['observed']
                if provider=='GITHUB':
                    expect={'repo_id':target['repo_id'], 'work_branch':target['work_branch'],
                            'tree_sha256':target['proposed_tree_sha256'], 'artifact_sha256':target['patch_sha256']}
                    if (receipt['provider']!='GITHUB' or not isinstance(observed,dict) or
                        set(observed) != set(expect)|{'commit_sha'} or
                        any(observed.get(k)!=v for k,v in expect.items()) or
                        not isinstance(observed.get('commit_sha'),str) or len(observed['commit_sha'])!=40):
                        return False
                    table='source_publications'; column='result_commit_sha'; revision=observed['commit_sha']
                else:
                    expect={'file_id':target['file_id'],'exported_sha256':target['exported_sha256']}
                    if (receipt['provider']!='GOOGLE_DRIVE' or not isinstance(observed,dict) or
                        set(observed) != set(expect)|{'revision'} or
                        any(observed.get(k)!=v for k,v in expect.items()) or
                        not isinstance(observed.get('revision'),str) or
                        observed['revision']==target['expected_revision']):
                        return False
                    table='document_publications'; column='resulting_revision'; revision=observed['revision']
                if row[5] in ('CONFLICT','BLOCKED'):
                    return False
                if row[5]=='VERIFIED':
                    prior=conn.execute('SELECT verified_receipt_json FROM nomad_v16.publication_intents WHERE publication_id=%s',
                                       (publication_id,)).fetchone()
                    saved=_json(prior[0]) if prior and prior[0] else None
                    # Verification timestamps and audit IDs can differ on retries.
                    return bool(saved and saved.get('observed') == observed and
                                saved.get('provider') == receipt['provider'] and
                                saved.get('idempotency_key') == idempotency_key and
                                (not saved.get('provider_request_id') or not receipt.get('provider_request_id') or
                                 saved.get('provider_request_id') == receipt.get('provider_request_id')))
                conn.execute(f'''UPDATE nomad_v16.{table} SET status='VERIFIED', {column}=%s,
                    observed_sha256=%s,provider_request_id=%s,verified_at=clock_timestamp() WHERE publication_id=%s''',
                    (revision,expect.get('tree_sha256',expect.get('exported_sha256')),
                     receipt['provider_request_id'],publication_id))
                conn.execute('''UPDATE nomad_v16.publication_intents SET status='VERIFIED',
                    verified_receipt_json=%s::jsonb, verified_at=clock_timestamp() WHERE publication_id=%s''',
                    (canonical_json(receipt),publication_id))
                conn.execute('''UPDATE nomad_v16.outbox SET status='DELIVERED',provider_ref=%s,
                    provider_effect_sha256=%s,claim_token=NULL,claim_expires_at=NULL
                    WHERE outbox_id=%s''', (revision,row[1].strip(),row[6]))
                return True
        except PublicationDenied:
            return False
