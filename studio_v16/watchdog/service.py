"""Deterministic watchdog operations. Scheduling/delivery are deployment-owned.

Run the auditor hourly and the watchdog under a distinct credential/process. This
module cannot detect a host outage by itself; use an external heartbeat monitor.
"""
from studio_v16.db.store import Denied, _fresh
from studio_v16.db.sqlite.publication import row
from studio_v16.db.sqlite.types import canonical, parse_json, integer
from studio_v16.communications.service import sha


class WatchdogService:
    def __init__(self, coordination, views, *, auditor_principal, watchdog_principal, source_sha,
                 director_department='STUDIO'):
        if auditor_principal == watchdog_principal or not auditor_principal or not watchdog_principal:
            raise ValueError('distinct independently authenticated auditor/watchdog principals required')
        self.coordination, self.views, self.owner = coordination, views, coordination.owner
        self.auditor, self.watchdog, self.source_sha = auditor_principal, watchdog_principal, source_sha
        self.director_department = director_department

    def _auth(self, conn, context, department, operation, expected, kind):
        self.coordination._auth(conn, context, department, operation)
        principal = row(conn, 'SELECT kind FROM principals WHERE principal_id=?', (context.principal_id,))
        if context.principal_id != expected or principal != {'kind': kind}:
            raise Denied('IDENTITY_UNVERIFIED')

    def record_audit(self, department, context):
        # Derive the report from current durable projections, not a submitted green claim.
        with self.owner.connection(readonly=True) as conn:
            self._auth(conn, context, department, 'AUDIT', self.auditor, 'AUDITOR')
        snapshot = self.views.snapshot(department, context)
        evidence = dict(source_sha=self.source_sha, snapshot=snapshot,
                        verdict='DEGRADED' if snapshot['missing_job_reports'] or any(r['observation'] == 'UNOBSERVED' for r in snapshot['responses']) else 'OBSERVED')
        with self.owner.write() as (conn, now):
            self._auth(conn, context, department, 'AUDIT', self.auditor, 'AUDITOR')
            evidence['observed_at'] = now
            conn.execute('''INSERT INTO supervisory_observations(observer_principal_id,subject_id,kind,evidence_sha256,
                evidence_json,observed_at) VALUES(?,?,'AUDIT',?,?,?) ON CONFLICT DO NOTHING''',
                (context.principal_id, department, sha(evidence), canonical(evidence), now))
        return evidence

    def record_leader_status(self, department, leader_epoch, context):
        snapshot = self.views.snapshot(department, context)
        with self.owner.write() as (conn, now):
            self.coordination._auth(conn, context, department, 'LEADER_STATUS')
            self.coordination._leader(conn, department, context.principal_id, leader_epoch)
            evidence = dict(source_sha=self.source_sha, leader_epoch=leader_epoch, observed_at=now, snapshot=snapshot)
            conn.execute('''INSERT INTO supervisory_observations(observer_principal_id,subject_id,kind,
                evidence_sha256,evidence_json,observed_at) VALUES(?,?,'LEADER_STATUS',?,?,?)''',
                (context.principal_id, department, sha(evidence), canonical(evidence), now))
            return evidence

    def inspect(self, department, context, *, stale_seconds=3900, response_stale_seconds=1200):
        integer(stale_seconds, minimum=60, maximum=86400)
        integer(response_stale_seconds, minimum=60, maximum=86400)
        with self.owner.write() as (conn, now):
            self._auth(conn, context, department, 'WATCHDOG', self.watchdog, 'WATCHDOG')
            audit = row(conn, '''SELECT * FROM supervisory_observations WHERE observer_principal_id=?
                AND subject_id=? AND kind='AUDIT' ORDER BY sequence DESC LIMIT 1''', (self.auditor, department))
            problems = []
            leader = row(conn, 'SELECT principal_id,leader_epoch FROM leader_state WHERE department=?', (department,))
            if leader:
                status = row(conn, '''SELECT * FROM supervisory_observations WHERE observer_principal_id=?
                    AND subject_id=? AND kind='LEADER_STATUS' ORDER BY sequence DESC LIMIT 1''', (leader['principal_id'], department))
                if (status is None or now-status['observed_at'] > stale_seconds*1000000 or
                        parse_json(status['evidence_json']).get('leader_epoch') != leader['leader_epoch']):
                    problems.append(('LEADER_UNOBSERVED', leader['principal_id']))
            if audit is None or now - audit['observed_at'] > stale_seconds * 1000000:
                problems.append(('AUDIT_UNAVAILABLE', department))
            else:
                evidence = parse_json(audit['evidence_json'])
                if sha(evidence) != audit['evidence_sha256'] or evidence.get('source_sha') != self.source_sha:
                    problems.append(('AUDIT_INVALID', department))
                elif evidence.get('verdict') == 'DEGRADED':
                    problems.append(('AUDIT_DEGRADED', department))
                elif evidence.get('verdict') != 'OBSERVED':
                    problems.append(('AUDIT_INVALID', department))
            # Missing terminal observations remain UNKNOWN, never inferred crashes.
            responses = conn.execute('''SELECT response_id FROM response_attempts WHERE department=?
                AND report_json IS NULL AND started_at<? ORDER BY started_at LIMIT 200''',
                (department, now - response_stale_seconds * 1000000)).fetchall()
            problems.extend(('RESPONSE_UNOBSERVED', r[0]) for r in responses)
            case_ids = []
            for reason, subject in problems:
                case = 'case_' + sha([department, reason, subject, self.source_sha])[:40]
                packet = dict(department=department, subject=subject, reason=reason, source_sha=self.source_sha,
                              observation='UNKNOWN' if reason == 'RESPONSE_UNOBSERVED' else 'DEGRADED',
                              owner_action='Review exact receipts and authorize recovery; no automatic promotion',
                              external_monitor='REQUIRED_FOR_HOST_OR_GATEWAY_OUTAGE')
                conn.execute('''INSERT INTO recovery_cases(case_id,department,subject_id,reason,status,owner_packet_json,
                    created_at,next_retry_at) VALUES(?,?,?,?,'ALERT_DELIVERY_PENDING',?,?,?) ON CONFLICT DO NOTHING''',
                    (case, department, subject, reason, canonical(packet), now, now))
                conn.execute('''INSERT INTO supervisory_observations(observer_principal_id,subject_id,kind,
                    evidence_sha256,evidence_json,observed_at) VALUES(?,?,'WATCHDOG',?,?,?) ON CONFLICT DO NOTHING''',
                    (context.principal_id, subject, sha(packet), canonical(packet), now))
                case_ids.append(case)
            if any(reason in ('AUDIT_UNAVAILABLE', 'AUDIT_INVALID', 'LEADER_UNOBSERVED') for reason, _ in problems):
                # Freeze new leadership decisions. Existing valid worker leases survive.
                if department == self.director_department:
                    conn.execute('UPDATE leader_state SET delegation_paused=1,updated_at=?', (now,))
                else:
                    conn.execute('UPDATE leader_state SET delegation_paused=1,updated_at=? WHERE department=?', (now, department))
            return dict(cases=case_ids, status='DEGRADED' if problems else 'OBSERVED', source_sha=self.source_sha,
                        auto_restart='UNSUPPORTED', independent_host_monitor='NOT_ATTESTED')

    def deliver_pending(self, department, context, deliver, *, limit=10, max_attempts=5):
        integer(limit, minimum=1, maximum=50)
        integer(max_attempts, minimum=1, maximum=10)
        claims = []
        with self.owner.write() as (conn, now):
            self._auth(conn, context, department, 'WATCHDOG', self.watchdog, 'WATCHDOG')
            pending = conn.execute('''SELECT case_id,owner_packet_json,alert_attempts FROM recovery_cases
                WHERE department=? AND status='ALERT_DELIVERY_PENDING' AND next_retry_at<=? AND alert_attempts<?
                AND (claim_token IS NULL OR claim_expires_at<?) ORDER BY next_retry_at,created_at LIMIT ?''',
                (department, now, max_attempts, now, limit)).fetchall()
            for case, packet, attempts in pending:
                token = _fresh('alert_')
                conn.execute('''UPDATE recovery_cases SET claim_token=?,claim_expires_at=?,alert_attempts=alert_attempts+1
                    WHERE case_id=?''', (token, now + 90000000, case))
                claims.append((case, packet, attempts + 1, token))
        result = []
        for case, packet, attempts, token in claims:
            # Adapter must perform idempotent delivery keyed by case ID and return
            # independently verified receipt, not API-accepted status.
            try:
                receipt = deliver(case, parse_json(packet))
                verified = isinstance(receipt, dict) and receipt.get('case_id') == case and receipt.get('readback_verified') is True
            except Exception:
                receipt, verified = None, False
            with self.owner.write() as (conn, now):
                self._auth(conn, context, department, 'WATCHDOG', self.watchdog, 'WATCHDOG')
                self.coordination.store._changed(conn.execute('''UPDATE recovery_cases SET status=?,delivery_receipt_json=?,
                    next_retry_at=?,claim_token=NULL,claim_expires_at=NULL WHERE case_id=? AND claim_token=?''',
                    ('ALERTED' if verified else 'ALERT_DELIVERY_PENDING', canonical(receipt) if verified else None,
                     now + min(3600, 2 ** attempts * 30) * 1000000, case, token)), 'STALE_LEASE')
            result.append(dict(case_id=case, status='ALERTED' if verified else 'ALERT_DELIVERY_PENDING', attempts=attempts))
        return result
