"""A06 SQL-owned manual response job selection; no V1 queue or Issue-label authority."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from studio_v16.db.authority_contract import Denied, GatewayContext
from studio_v16.db.sqlite.types import utc_us
from studio_v16.db.sqlite.publication import row


class SQLiteClaimSelector:
    def __init__(self, coordination, *, lease_seconds=300):
        if not 30 <= lease_seconds <= 600:
            raise ValueError('bounded claim lease required')
        self.coordination = coordination
        self.lease_seconds = lease_seconds

    def next(self, response_id, principal):
        """Select the next authenticated, owner-approved job from SQL only.

        This read-only candidate is revalidated by SQLiteStore._claim in the
        same transaction that acquires the lease, so concurrent claims cannot
        both succeed. The caller must not change returned trusted fields.
        """
        if principal is None or principal.auth_method != 'MTLS':
            raise Denied('IDENTITY_UNVERIFIED')
        with self.coordination.owner.connection(readonly=True) as conn:
            response = row(conn, 'SELECT * FROM response_attempts WHERE response_id=?', (response_id,))
            if not response or response['principal_id'] != principal.principal_id or response['status'] != 'RUNNING':
                raise Denied('ACCESS_REQUIRED')
            self.coordination._auth(conn, GatewayContext(principal.principal_id, verified_gateway=True),
                                    response['department'], 'RESPONSE')
            stopped = conn.execute("SELECT 1 FROM response_controls WHERE response_id=? AND action='STOP' LIMIT 1", (response_id,)).fetchone()
            control = conn.execute('SELECT action FROM response_controls WHERE response_id=? ORDER BY control_epoch DESC LIMIT 1', (response_id,)).fetchone()
            if stopped or (control and control[0] in ('DRAIN','PAUSE','STOP')):
                return None
            now = utc_us()
            candidate = row(conn, """SELECT j.job_id,j.source_base_sha,j.resource_key,j.authority_revision,
                j.assignment_epoch,a.worker_id,g.grant_id,g.issuer_approval_ref,
                COALESCE(l.lease_generation + 1,1) AS next_lease_generation
                FROM jobs j JOIN worker_assignments a ON a.job_id=j.job_id
                JOIN grants g ON g.job_id=j.job_id AND g.worker_id=a.worker_id
                JOIN decisions d ON d.decision_id=g.issuer_approval_ref
                JOIN principals issuer ON issuer.principal_id=d.authority_principal_id
                LEFT JOIN resource_leases l ON l.repo_id=j.source_repo AND l.resource_key=j.resource_key
                WHERE j.state IN ('READY','YIELDED') AND a.status='ACTIVE' AND a.revoked_at IS NULL
                AND a.assignment_epoch=j.assignment_epoch AND a.worker_id=?
                AND g.principal_id=? AND g.operation='CLAIM' AND g.revoked_at IS NULL
                AND g.expires_at>? AND g.repo_id=j.source_repo AND g.assignment_epoch=j.assignment_epoch
                AND g.source_base_sha=j.source_base_sha AND g.authority_revision=j.authority_revision
                AND g.approved_scope_hash=j.approved_scope_hash
                AND d.job_id=j.job_id AND d.action='CLAIM' AND d.revoked_at IS NULL
                AND (d.expires_at IS NULL OR d.expires_at>?)
                AND d.authority_revision=j.authority_revision AND d.approved_scope_hash=j.approved_scope_hash
                AND issuer.status='ACTIVE'
                AND (l.holder_attempt_id IS NULL OR l.expires_at<=?)
                AND NOT EXISTS (SELECT 1 FROM job_dependencies dep JOIN jobs prior
                    ON prior.job_id=dep.prerequisite_job_id
                    WHERE dep.job_id=j.job_id AND prior.state<>'DONE')
                AND NOT EXISTS (SELECT 1 FROM budget_reservations b WHERE b.job_id=j.job_id
                    AND b.amount>0 AND b.status<>'APPROVED')
                ORDER BY j.created_at,j.job_id LIMIT 1""",
                (response['worker_id'], principal.principal_id, now, now, now))
        if candidate is None:
            return None
        expiry = datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)
        return dict(schema_id='DB-CONTRACT/COMMAND/1',command_id='command_'+secrets.token_hex(12),
                    command_kind='CLAIM',job_id=candidate['job_id'],attempt_id='attempt_'+secrets.token_hex(12),
                    worker_id=candidate['worker_id'],assignment_epoch=candidate['assignment_epoch'],
                    authority_revision=candidate['authority_revision'],
                    payload=dict(source_base_sha=candidate['source_base_sha'],resource_key=candidate['resource_key'],
                                 grant_id=candidate['grant_id'],owner_decision_ref=candidate['issuer_approval_ref'],
                                 expected_lease_generation=candidate['next_lease_generation'],
                                 lease_expires_at=expiry.isoformat().replace('+00:00','Z')))
