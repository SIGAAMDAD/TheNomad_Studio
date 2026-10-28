"""Explicit SQLite DB-CONTRACT command implementation; no SQL dialect rewriting."""
from __future__ import annotations

import hashlib
import sqlite3

from ..store import (COMMAND_VALIDATOR, EVENT_VALIDATOR, RESUME_VALIDATOR,
                     Denied, GatewayContext, PostgresStore, _fresh, _utc)
from .connection import ConnectionOwner, Unavailable
from .migrate import migrate
from .types import canonical, epoch_us, integer, parse_json


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


class SQLiteStore:
    _result = staticmethod(PostgresStore._result)
    _deny_if_untrusted = staticmethod(PostgresStore._deny_if_untrusted)

    def __init__(self, owner: ConnectionOwner, *, apply_migrations=True):
        self.owner = owner
        if apply_migrations:
            migrate(owner)

    def close(self):
        self.owner.close()

    def _principal(self, conn, context):
        self._deny_if_untrusted(context)
        row = conn.execute('SELECT status FROM principals WHERE principal_id=?',
                           (context.principal_id,)).fetchone()
        if not row or row[0] != 'ACTIVE':
            raise Denied('IDENTITY_UNVERIFIED')

    def _authority(self, conn, cmd, ctx, operation, now, *, require_attempt=True):
        self._principal(conn, ctx)
        row = conn.execute('''SELECT state,authority_revision,source_repo,source_base_sha,
            approved_scope_hash,resource_key,assignment_epoch,version FROM jobs WHERE job_id=?''',
            (cmd['job_id'],)).fetchone()
        if not row:
            raise Denied('ACCESS_REQUIRED')
        state, revision, repo, base, scope, resource, epoch, version = row
        if revision != cmd['authority_revision']:
            raise Denied('AUTHORITY_REVISION_MISMATCH')
        if state in ('CANCELLED', 'DONE', 'BLOCKED'):
            raise Denied('ACCESS_REQUIRED')
        if epoch != cmd['assignment_epoch']:
            raise Denied('STALE_ASSIGNMENT')
        active = conn.execute('''SELECT 1 FROM worker_assignments WHERE job_id=?
            AND worker_id=? AND assignment_epoch=? AND status='ACTIVE' AND revoked_at IS NULL''',
            (cmd['job_id'], cmd['worker_id'], epoch)).fetchone()
        if not active:
            raise Denied('STALE_ASSIGNMENT')
        permit = conn.execute('''SELECT g.grant_id,g.expires_at,g.revoked_at,g.assignment_epoch,
            g.source_base_sha,d.revoked_at,d.expires_at,d.approved_scope_hash,d.authority_revision,
            d.decision_id FROM grants g JOIN decisions d ON d.decision_id=g.issuer_approval_ref
            JOIN principals issuer ON issuer.principal_id=d.authority_principal_id
            WHERE g.principal_id=? AND g.job_id=? AND g.worker_id=? AND g.repo_id=?
            AND g.operation=? AND g.authority_revision=? AND g.approved_scope_hash=?
            AND d.job_id=g.job_id AND d.approved_scope_hash=g.approved_scope_hash
            AND d.action=g.operation AND issuer.status='ACTIVE'
            ORDER BY g.expires_at DESC,g.grant_id LIMIT 1''',
            (ctx.principal_id, cmd['job_id'], cmd['worker_id'], repo, operation, revision, scope)).fetchone()
        if not permit:
            raise Denied('ACCESS_REQUIRED')
        grant, expiry, revoked, grant_epoch, grant_base, dr, de, ds, dv, decision = permit
        if revoked is not None or dr is not None:
            raise Denied('REVOKED_GRANT')
        if expiry <= now or (de is not None and de <= now):
            raise Denied('EXPIRED_GRANT')
        if grant_epoch != epoch:
            raise Denied('STALE_ASSIGNMENT')
        if grant_base != base or dv != revision or ds != scope:
            raise Denied('REBASE_REQUIRED')
        if require_attempt:
            attempt = conn.execute('''SELECT principal_id,status,lease_generation FROM attempts
                WHERE attempt_id=? AND job_id=? AND worker_id=? AND assignment_epoch=?''',
                (cmd['attempt_id'], cmd['job_id'], cmd['worker_id'], epoch)).fetchone()
            lease = conn.execute('''SELECT lease_generation,holder_attempt_id,expires_at,baseline_sha
                FROM resource_leases WHERE repo_id=? AND resource_key=?''', (repo, resource)).fetchone()
            if (not attempt or attempt[0] != ctx.principal_id or attempt[1] in ('STOPPED', 'SUBMITTED')
                    or not lease or lease[0] != attempt[2] or lease[1] != cmd['attempt_id']
                    or lease[2] <= now or lease[3] != base):
                raise Denied('STALE_LEASE')
        return dict(job_id=cmd['job_id'], repo=repo, base=base, scope=scope,
                    resource=resource, epoch=epoch, version=version, grant_id=grant, decision_id=decision)

    @staticmethod
    def _changed(cursor, reason='STALE_ASSIGNMENT'):
        if cursor.rowcount != 1:
            raise Denied(reason)

    @staticmethod
    def _saved_result(conn, cmd, payload_hash):
        row = conn.execute('SELECT payload_sha256,result_json FROM command_results WHERE command_id=?',
                           (cmd['command_id'],)).fetchone()
        if not row:
            return None
        if row[0] != payload_hash:
            raise Denied('IDEMPOTENCY_CONFLICT')
        return dict(parse_json(row[1]), result='DUPLICATE', commit_sequence=None)

    def execute(self, command: dict, context: GatewayContext) -> dict:
        cmd = command
        try:
            COMMAND_VALIDATOR.validate(cmd)
            payload_hash = digest(cmd)
        except Exception:
            return self._result('invalid', 'DENIED', 'MALFORMED_REQUEST')
        try:
            self._deny_if_untrusted(context)
            with self.owner.write() as (conn, now):
                self._principal(conn, context)
                saved = self._saved_result(conn, cmd, payload_hash)
                if saved:
                    return saved
                handlers = {'CLAIM': self._claim, 'CHECKPOINT': self._checkpoint,
                            'REASSIGN': self._reassign, 'ACK': self._ack,
                            'OUTBOX_SCHEDULE': self._schedule, 'OUTBOX_ACK': self._outbox_ack}
                if cmd['command_kind'] in ('RESERVE_PUBLICATION', 'RECONCILE_PUBLICATION'):
                    raise Denied('GATEWAY_UNAVAILABLE')
                handler = handlers.get(cmd['command_kind'])
                if handler is None:
                    raise Denied('MALFORMED_REQUEST')
                data = handler(conn, cmd, context, now)
                event = data.get('event_id') or self._journal_internal(conn, cmd, now)
                # Store the complete result in one immutable INSERT. The immediate
                # writer reservation protects allocation, including explicit imports.
                seq = conn.execute('''SELECT max(COALESCE((SELECT seq FROM sqlite_sequence
                    WHERE name='command_results'),0),COALESCE((SELECT max(commit_sequence)
                    FROM command_results),0))+1''').fetchone()[0]
                integer(seq, minimum=1)
                result = self._result(cmd['command_id'], 'COMMITTED', None, seq,
                                      data.get('lease_generation'), event, data.get('checkpoint_sha256'))
                conn.execute('''INSERT INTO command_results
                    (commit_sequence,command_id,payload_sha256,result_json,created_at) VALUES(?,?,?,?,?)''',
                    (seq, cmd['command_id'], payload_hash, canonical(result), now))
            return result
        except Denied as exc:
            return self._result(cmd['command_id'], 'DENIED', exc.reason)
        except (Unavailable, sqlite3.DatabaseError, OverflowError):
            # COMMIT may have succeeded before the connection failed. Read the
            # exact durable identity before returning an unavailable result.
            try:
                with self.owner.connection(readonly=True) as conn:
                    self._principal(conn, context)
                    saved = self._saved_result(conn, cmd, payload_hash)
                    if saved:
                        return saved
            except (Unavailable, sqlite3.DatabaseError, Denied):
                pass
            return self._result(cmd['command_id'], 'DENIED', 'RETRYABLE_BLOCKED')

    command = execute

    @staticmethod
    def _journal_internal(conn, cmd, now):
        event = _fresh('evt_')
        kinds = {'CLAIM': 'STARTED', 'REASSIGN': 'HANDOFF_READY', 'ACK': 'ACK',
                 'OUTBOX_SCHEDULE': 'HANDOFF_READY', 'OUTBOX_ACK': 'ACK',
                 'RESERVE_PUBLICATION': 'HANDOFF_READY'}
        kind = (cmd['payload'].get('state', 'CHECKPOINT').replace('CHECKPOINTED', 'CHECKPOINT')
                if cmd['command_kind'] == 'CHECKPOINT' else kinds[cmd['command_kind']])
        envelope = dict(schema_id='DB-CONTRACT/EVENT/1', event_id=event, producer='SQLITE_SHADOW',
                        transport_delivery_id='cmd:' + cmd['command_id'], job_id=cmd['job_id'],
                        worker_id=cmd['worker_id'], attempt_id=cmd['attempt_id'],
                        assignment_epoch=cmd['assignment_epoch'], event_kind=kind,
                        causation_id=None, correlation_id=cmd['command_id'], payload_sha256=digest(cmd),
                        immutable_evidence_ref='urn:nomad:db-command:' + hashlib.sha256(cmd['command_id'].encode()).hexdigest(),
                        received_at=_utc(), delivery_authority='ARCHIVE_ONLY')
        EVENT_VALIDATOR.validate(envelope)
        conn.execute('''INSERT INTO event_journal(event_id,producer,transport_delivery_id,
            partition_key,payload_sha256,canonical_event_json,received_at) VALUES(?,?,?,?,?,?,?)''',
            (event, 'SQLITE_SHADOW', envelope['transport_delivery_id'], cmd['job_id'],
             digest(cmd), canonical(envelope), now))
        return event

    def _claim(self, conn, cmd, ctx, now):
        j = self._authority(conn, cmd, ctx, 'CLAIM', now, require_attempt=False)
        p = cmd['payload']
        if p['source_base_sha'] != j['base'] or not ctx.verified_source_base:
            raise Denied('REBASE_REQUIRED')
        if (p['resource_key'] != j['resource'] or p['grant_id'] != j['grant_id']
                or p['owner_decision_ref'] != j['decision_id']):
            raise Denied('INVALID_SCOPE')
        if conn.execute('''SELECT 1 FROM job_dependencies d JOIN jobs j
            ON j.job_id=d.prerequisite_job_id WHERE d.job_id=? AND j.state<>'DONE' LIMIT 1''',
            (cmd['job_id'],)).fetchone():
            raise Denied('RETRYABLE_BLOCKED')
        if conn.execute("SELECT 1 FROM budget_reservations WHERE job_id=? AND amount>0 AND status<>'APPROVED' LIMIT 1",
                        (cmd['job_id'],)).fetchone():
            raise Denied('BUDGET_DENIED')
        if conn.execute('SELECT 1 FROM attempts WHERE attempt_id=?', (cmd['attempt_id'],)).fetchone():
            raise Denied('IDEMPOTENCY_CONFLICT')
        expiry = epoch_us(p['lease_expires_at'])
        if expiry <= now:
            raise Denied('STALE_LEASE')
        lease = conn.execute('''SELECT lease_generation,holder_attempt_id,expires_at
            FROM resource_leases WHERE repo_id=? AND resource_key=?''', (j['repo'], j['resource'])).fetchone()
        if lease:
            if lease[0] != p['expected_lease_generation']:
                raise Denied('STALE_LEASE')
            if lease[1] is not None and lease[2] > now:
                raise Denied('RETRYABLE_BLOCKED')
            generation = integer(lease[0] + 1, minimum=1)
        else:
            if p['expected_lease_generation'] != 1:
                raise Denied('STALE_LEASE')
            generation = 1
        conn.execute('''INSERT INTO attempts(attempt_id,job_id,worker_id,principal_id,
            assignment_epoch,status,source_base_sha,lease_generation,started_at)
            VALUES(?,?,?,?,?,'STARTED',?,?,?)''',
            (cmd['attempt_id'], cmd['job_id'], cmd['worker_id'], ctx.principal_id,
             j['epoch'], j['base'], generation, now))
        if lease:
            self._changed(conn.execute('''UPDATE resource_leases SET lease_generation=?,holder_attempt_id=?,
                expires_at=?,baseline_sha=? WHERE repo_id=? AND resource_key=? AND lease_generation=?''',
                (generation, cmd['attempt_id'], expiry, j['base'], j['repo'], j['resource'], lease[0])), 'STALE_LEASE')
        else:
            conn.execute('INSERT INTO resource_leases VALUES(?,?,?,?,?,?)',
                         (j['repo'], j['resource'], generation, cmd['attempt_id'], expiry, j['base']))
        self._changed(conn.execute("UPDATE jobs SET state='CLAIMED',version=version+1 WHERE job_id=? AND version=? AND assignment_epoch=?",
                                   (j['job_id'], j['version'], j['epoch'])))
        return dict(lease_generation=generation)

    def _checkpoint(self, conn, cmd, ctx, now):
        j = self._authority(conn, cmd, ctx, 'CHECKPOINT', now)
        p = cmd['payload']
        if p['source_base_sha'] != j['base']:
            raise Denied('REBASE_REQUIRED')
        terminal = p['state'] in ('YIELDED', 'SUBMITTED', 'STOPPED')
        self._changed(conn.execute('''UPDATE attempts SET status=?,checkpoint_sha=?,artifact_ref=?,
            elapsed_observed_seconds=?,resume_required=?,ended_at=? WHERE attempt_id=? AND assignment_epoch=?''',
            (p['state'], p['checkpoint_sha256'], p['artifact_ref'], p['elapsed_observed_seconds'],
             int(p['resume_required']), now if terminal else None, cmd['attempt_id'], j['epoch'])))
        if terminal:
            conn.execute('UPDATE resource_leases SET holder_attempt_id=NULL WHERE holder_attempt_id=?', (cmd['attempt_id'],))
            state = {'YIELDED': 'YIELDED', 'SUBMITTED': 'SUBMITTED', 'STOPPED': 'READY'}[p['state']]
            self._changed(conn.execute('UPDATE jobs SET state=?,version=version+1 WHERE job_id=? AND version=?',
                                      (state, j['job_id'], j['version'])))
        conn.execute('''INSERT INTO recovery_checkpoints(checkpoint_id,job_id,attempt_id,source_ref,
            snapshot_hash,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(attempt_id,snapshot_hash) DO NOTHING''',
            (_fresh('cp_'), j['job_id'], cmd['attempt_id'], p['artifact_ref'], p['checkpoint_sha256'], now))
        return dict(checkpoint_sha256=p['checkpoint_sha256'])

    def _reassign(self, conn, cmd, ctx, now):
        j = self._authority(conn, cmd, ctx, 'REASSIGN', now, require_attempt=False)
        p = cmd['payload']
        decision = conn.execute('''SELECT 1 FROM decisions d JOIN principals owner
            ON owner.principal_id=d.authority_principal_id WHERE d.decision_id=? AND d.job_id=?
            AND d.action='REASSIGN' AND d.revoked_at IS NULL AND d.authority_revision=?
            AND d.approved_scope_hash=? AND owner.status='ACTIVE' AND (d.expires_at IS NULL OR d.expires_at>?)''',
            (p['revocation_decision_ref'], j['job_id'], cmd['authority_revision'], j['scope'], now)).fetchone()
        if not decision:
            raise Denied('OWNER_APPROVAL_REQUIRED')
        if p['new_assignment_epoch'] != j['epoch'] + 1:
            raise Denied('STALE_ASSIGNMENT')
        lease = conn.execute('SELECT lease_generation FROM resource_leases WHERE repo_id=? AND resource_key=?',
                             (j['repo'], j['resource'])).fetchone()
        if not lease or p['new_lease_generation'] != lease[0] + 1:
            raise Denied('STALE_LEASE')
        if p['transfer_checkpoint_sha256'] is not None and not conn.execute(
                'SELECT 1 FROM attempts WHERE job_id=? AND checkpoint_sha=?',
                (j['job_id'], p['transfer_checkpoint_sha256'])).fetchone():
            raise Denied('SOURCE_SHA_MISMATCH')
        self._changed(conn.execute("UPDATE worker_assignments SET status='REVOKED',revoked_at=? WHERE job_id=? AND status='ACTIVE' AND assignment_epoch=?",
                                   (now, j['job_id'], j['epoch'])))
        conn.execute("INSERT INTO worker_assignments(job_id,worker_id,assignment_epoch,status,valid_from) VALUES(?,?,?,'ACTIVE',?)",
                     (j['job_id'], p['new_worker_id'], p['new_assignment_epoch'], now))
        self._changed(conn.execute("UPDATE jobs SET assignment_epoch=?,state='READY',version=version+1 WHERE job_id=? AND assignment_epoch=? AND version=?",
                                   (p['new_assignment_epoch'], j['job_id'], j['epoch'], j['version'])))
        self._changed(conn.execute('''UPDATE resource_leases SET holder_attempt_id=NULL,lease_generation=?,expires_at=?
            WHERE repo_id=? AND resource_key=? AND lease_generation=?''',
            (p['new_lease_generation'], now, j['repo'], j['resource'], lease[0])), 'STALE_LEASE')
        return dict(lease_generation=p['new_lease_generation'], checkpoint_sha256=p['transfer_checkpoint_sha256'])

    def _ack(self, conn, cmd, ctx, now):
        self._authority(conn, cmd, ctx, 'ACK', now)
        p = cmd['payload']
        if p['recipient_worker_id'] != cmd['worker_id'] or p['delivery_assignment_epoch'] != cmd['assignment_epoch']:
            raise Denied('STALE_ASSIGNMENT')
        event = conn.execute('SELECT sequence FROM event_journal WHERE event_id=? AND partition_key=?',
                             (p['event_id'], cmd['job_id'])).fetchone()
        if not event:
            raise Denied('ACCESS_REQUIRED')
        conn.execute('''INSERT INTO delivery_effects(event_id,consumer_id,assignment_epoch,effect_name,recorded_at)
            VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING''',
            (p['event_id'], cmd['worker_id'], cmd['assignment_epoch'], p['effect_name'], now))
        # Reordered duplicate ACKs must never move a cursor backwards.
        conn.execute('''INSERT INTO inbox_cursors(job_id,worker_id,assignment_epoch,last_event_id,updated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(job_id,worker_id,assignment_epoch) DO UPDATE
            SET last_event_id=excluded.last_event_id,updated_at=excluded.updated_at
            WHERE (SELECT sequence FROM event_journal WHERE event_id=inbox_cursors.last_event_id)<?''',
            (cmd['job_id'], cmd['worker_id'], cmd['assignment_epoch'], p['event_id'], now, event[0]))
        return dict(event_id=p['event_id'])

    def _schedule(self, conn, cmd, ctx, now):
        self._authority(conn, cmd, ctx, 'OUTBOX_SCHEDULE', now)
        p = cmd['payload']
        # Publication effects require the full intent path, never a generic queue item.
        if p['kind'] in ('GITHUB_EFFECT', 'DRIVE_EFFECT'):
            raise Denied('GATEWAY_UNAVAILABLE')
        row = conn.execute('SELECT payload_sha256 FROM event_journal WHERE event_id=? AND partition_key=?',
                           (p['event_id'], cmd['job_id'])).fetchone()
        if not row or row[0] != p['payload_sha256']:
            raise Denied('SOURCE_SHA_MISMATCH')
        row = conn.execute('SELECT payload_sha256 FROM outbox WHERE event_id=? AND destination=? AND kind=?',
                           (p['event_id'], p['destination'], p['kind'])).fetchone()
        if row and row[0] != p['payload_sha256']:
            raise Denied('IDEMPOTENCY_CONFLICT')
        conn.execute('''INSERT INTO outbox(outbox_id,event_id,kind,destination,status,payload_sha256,next_retry_at,created_at)
            VALUES(?,?,?,?,'PREPARED',?,?,?) ON CONFLICT(event_id,destination,kind) DO NOTHING''',
            (_fresh('out_'), p['event_id'], p['kind'], p['destination'], p['payload_sha256'], now, now))
        return dict(event_id=p['event_id'])

    def _outbox_ack(self, conn, cmd, ctx, now):
        self._authority(conn, cmd, ctx, 'OUTBOX_ACK', now)
        p = cmd['payload']
        if not ctx.verified_external_receipt:
            raise Denied('ACCESS_REQUIRED')
        row = conn.execute('''SELECT o.payload_sha256,o.status,o.provider_ref FROM outbox o
            JOIN event_journal e ON e.event_id=o.event_id WHERE o.outbox_id=? AND e.partition_key=?
            AND o.kind NOT IN ('GITHUB_EFFECT','DRIVE_EFFECT')''', (p['outbox_id'], cmd['job_id'])).fetchone()
        if not row:
            raise Denied('ACCESS_REQUIRED')
        if row[0] != p['effect_sha256']:
            raise Denied('SOURCE_SHA_MISMATCH')
        if row[1] == 'DELIVERED':
            if row[2] != p['provider_ref']:
                raise Denied('IDEMPOTENCY_CONFLICT')
            return {}
        if row[1] not in ('IN_FLIGHT', 'RECONCILE_REQUIRED'):
            raise Denied('RETRYABLE_BLOCKED')
        self._changed(conn.execute('''UPDATE outbox SET status='DELIVERED',provider_ref=?,provider_effect_sha256=?,
            claim_token=NULL,claim_expires_at=NULL WHERE outbox_id=? AND status=?''',
            (p['provider_ref'], p['effect_sha256'], p['outbox_id'], row[1])))
        return {}

    def record_event(self, envelope, context):
        self._deny_if_untrusted(context)
        EVENT_VALIDATOR.validate(envelope)
        with self.owner.write() as (conn, now):
            self._principal(conn, context)
            row = conn.execute('''SELECT event_id,payload_sha256 FROM event_journal
                WHERE producer=? AND transport_delivery_id=?''',
                (envelope['producer'], envelope['transport_delivery_id'])).fetchone()
            if row:
                if row != (envelope['event_id'], envelope['payload_sha256']):
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return row[0]
            if conn.execute('SELECT 1 FROM event_journal WHERE event_id=?', (envelope['event_id'],)).fetchone():
                raise Denied('IDEMPOTENCY_CONFLICT')
            conn.execute('''INSERT INTO event_journal(event_id,producer,transport_delivery_id,partition_key,
                payload_sha256,canonical_event_json,immutable_evidence_ref,received_at) VALUES(?,?,?,?,?,?,?,?)''',
                (envelope['event_id'], envelope['producer'], envelope['transport_delivery_id'], envelope['job_id'],
                 envelope['payload_sha256'], canonical(envelope), envelope['immutable_evidence_ref'], now))
        return envelope['event_id']

    def reserve_outbox_delivery(self, context, max_items=20, max_attempts=5, lease_seconds=90, *, shadow_test=False):
        self._deny_if_untrusted(context)
        for value, upper in ((max_items, 100), (max_attempts, 20), (lease_seconds, 600)):
            integer(value, minimum=1, maximum=upper)
        with self.owner.write() as (conn, now):
            self._principal(conn, context)
            mode = conn.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone()
            # A test flag cannot enable external delivery from SHADOW.
            if not mode or mode[0] != 'ACTIVE' or shadow_test:
                raise Denied('CUTOVER_NOT_APPROVED')
            conn.execute('''UPDATE outbox SET status='RECONCILE_REQUIRED',claim_token=NULL,claim_expires_at=NULL
                WHERE status='IN_FLIGHT' AND claim_expires_at<?''', (now,))
            conn.execute("UPDATE outbox SET status='DEAD_LETTER',dead_letter_reason='RETRY_LIMIT' WHERE status='RETRY' AND attempts>=?", (max_attempts,))
            rows = conn.execute('''SELECT outbox_id,event_id,kind,destination,payload_sha256,attempts
                FROM outbox WHERE kind NOT IN ('GITHUB_EFFECT','DRIVE_EFFECT')
                AND status IN ('PREPARED','RETRY') AND next_retry_at<=?
                ORDER BY next_retry_at,created_at,outbox_id LIMIT ?''', (now, max_items)).fetchall()
            results = []
            for row in rows:
                token = _fresh('claim_')
                self._changed(conn.execute('''UPDATE outbox SET status='IN_FLIGHT',attempts=attempts+1,
                    claim_token=?,claim_expires_at=? WHERE outbox_id=? AND status IN ('PREPARED','RETRY')''',
                    (token, now + lease_seconds * 1000000, row[0])), 'STALE_LEASE')
                results.append(dict(zip(('outbox_id', 'event_id', 'kind', 'destination', 'payload_sha256'), row[:5]),
                                    attempts=row[5] + 1, claim_token=token))
            return results

    def mark_delivery_retry(self, outbox_id, claim_token, reason, context, max_attempts=5):
        self._deny_if_untrusted(context)
        with self.owner.write() as (conn, now):
            self._principal(conn, context)
            self._changed(conn.execute('''UPDATE outbox SET status='RECONCILE_REQUIRED',claim_token=NULL,
                claim_expires_at=NULL,dead_letter_reason=? WHERE outbox_id=? AND claim_token=?
                AND kind NOT IN ('GITHUB_EFFECT','DRIVE_EFFECT') AND status='IN_FLIGHT' ''',
                (str(reason)[:200], outbox_id, claim_token)), 'STALE_LEASE')
        return 'RECONCILE_REQUIRED'

    def reconcile_outbox(self, outbox_id, readback_state, observed_sha256, provider_ref, context, *, max_attempts=5):
        self._deny_if_untrusted(context)
        integer(max_attempts, minimum=1, maximum=20)
        if not context.provider_readback_verified:
            raise Denied('ACCESS_REQUIRED')
        if readback_state not in ('PRESENT', 'ABSENT', 'CONFLICT'):
            raise ValueError('unsupported readback')
        with self.owner.write() as (conn, now):
            self._principal(conn, context)
            row = conn.execute('''SELECT payload_sha256,status,attempts,provider_ref FROM outbox
                WHERE outbox_id=? AND kind NOT IN ('GITHUB_EFFECT','DRIVE_EFFECT')''', (outbox_id,)).fetchone()
            if not row:
                raise Denied('ACCESS_REQUIRED')
            sha, status, attempts, previous = row
            if status == 'DELIVERED':
                if readback_state != 'PRESENT' or observed_sha256 != sha or provider_ref != previous:
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return status
            if status not in ('IN_FLIGHT', 'RECONCILE_REQUIRED'):
                raise Denied('RETRYABLE_BLOCKED')
            if readback_state == 'PRESENT':
                new = 'DELIVERED' if observed_sha256 == sha and provider_ref else 'BLOCKED'
            elif readback_state == 'ABSENT':
                mode = conn.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone()
                if not mode or mode[0] != 'ACTIVE':
                    return 'RECONCILE_REQUIRED'
                new = 'DEAD_LETTER' if attempts >= max_attempts else 'RETRY'
            else:
                new = 'BLOCKED'
            reason = {'BLOCKED': 'READBACK_CONFLICT', 'DEAD_LETTER': 'RETRY_LIMIT'}.get(new)
            conn.execute('''UPDATE outbox SET status=?,provider_ref=?,provider_effect_sha256=?,dead_letter_reason=?,
                claim_token=NULL,claim_expires_at=NULL,next_retry_at=? WHERE outbox_id=?''',
                (new, provider_ref if new == 'DELIVERED' else previous, observed_sha256 if new == 'DELIVERED' else None,
                 reason, now + min(3600, 2 ** min(attempts, 11)) * 1000000, outbox_id))
            return new

    def shadow_import(self, import_id, source_kind, source_ref, source_revision, source_json, context):
        self._deny_if_untrusted(context)
        if source_kind not in ('GITHUB', 'DRIVE', 'LEDGER'):
            raise ValueError('unknown source kind')
        sha = digest(source_json)
        with self.owner.write() as (conn, now):
            self._principal(conn, context)
            if conn.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone() != ('SHADOW',):
                raise Denied('CUTOVER_NOT_APPROVED')
            row = conn.execute('''SELECT import_id,source_kind,source_ref,source_revision,content_sha256
                FROM shadow_imports WHERE import_id=? OR (source_kind=? AND source_ref=? AND source_revision=?)''',
                (import_id, source_kind, source_ref, source_revision)).fetchall()
            if row:
                if len(row) != 1 or row[0][1:] != (source_kind, source_ref, source_revision, sha):
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return sha
            conn.execute('''INSERT INTO shadow_imports(import_id,source_kind,source_ref,source_revision,
                content_sha256,source_json,imported_at) VALUES(?,?,?,?,?,?,?)''',
                (import_id, source_kind, source_ref, source_revision, sha, canonical(source_json), now))
        return sha

    def reconcile_shadow(self, source_kind, source_ref, source_revision, expected_digest, context):
        self._deny_if_untrusted(context)
        if not context.provider_readback_verified:
            raise Denied('ACCESS_REQUIRED')
        with self.owner.write() as (conn, now):
            self._principal(conn, context)
            row = conn.execute('''SELECT content_sha256 FROM shadow_imports
                WHERE source_kind=? AND source_ref=? AND source_revision=?''',
                (source_kind, source_ref, source_revision)).fetchone()
            if not row:
                raise Denied('ACCESS_REQUIRED')
            status = 'MATCH' if row[0] == expected_digest else 'MISMATCH'
            conn.execute('''UPDATE shadow_imports SET reconciliation_status=?
                WHERE source_kind=? AND source_ref=? AND source_revision=?''',
                (status, source_kind, source_ref, source_revision))
            return status

    def save_resume_capsule(self, capsule, context):
        self._deny_if_untrusted(context)
        RESUME_VALIDATOR.validate(capsule)
        with self.owner.write() as (conn, now):
            self._principal(conn, context)
            row = conn.execute('''SELECT a.worker_id,a.assignment_epoch,a.lease_generation,a.source_base_sha,
                a.checkpoint_sha,a.principal_id,j.source_repo,j.assignment_epoch FROM attempts a
                JOIN jobs j ON j.job_id=a.job_id WHERE a.job_id=? AND a.attempt_id=?''',
                (capsule['job_id'], capsule['attempt_id'])).fetchone()
            if not row or row[5] != context.principal_id:
                raise Denied('ACCESS_REQUIRED')
            if row[1] != capsule['assignment_epoch'] or row[7] != capsule['assignment_epoch']:
                raise Denied('STALE_ASSIGNMENT')
            expected = (capsule['worker_id'], capsule['lease_generation'], capsule['source_base_sha'],
                        capsule['checkpoint_sha256'], capsule['source_repo'])
            if (row[0], row[2], row[3], row[4], row[6]) != expected:
                raise Denied('SOURCE_SHA_MISMATCH')
            raw = canonical(capsule)
            prior = conn.execute('''SELECT capsule_json FROM resume_capsules
                WHERE job_id=? AND attempt_id=? AND checkpoint_sha256=?''',
                (capsule['job_id'], capsule['attempt_id'], capsule['checkpoint_sha256'])).fetchone()
            if prior:
                if prior[0] != raw:
                    raise Denied('IDEMPOTENCY_CONFLICT')
            else:
                conn.execute('''INSERT INTO resume_capsules(job_id,attempt_id,checkpoint_sha256,capsule_json,captured_at)
                    VALUES(?,?,?,?,?)''', (capsule['job_id'], capsule['attempt_id'], capsule['checkpoint_sha256'], raw, now))
        return digest(capsule)

    def load_resume_capsule(self, job_id, context):
        self._deny_if_untrusted(context)
        with self.owner.connection(readonly=True) as conn:
            self._principal(conn, context)
            row = conn.execute('''SELECT r.capsule_json FROM resume_capsules r JOIN attempts a
                ON a.attempt_id=r.attempt_id JOIN jobs j ON j.job_id=a.job_id
                WHERE r.job_id=? AND a.principal_id=? AND a.assignment_epoch=j.assignment_epoch
                ORDER BY r.captured_at DESC,a.started_at DESC,r.attempt_id DESC LIMIT 1''',
                (job_id, context.principal_id)).fetchone()
            if row is None:
                return None
            value = parse_json(row[0])
            RESUME_VALIDATOR.validate(value)
            return value

    def resume_capsule(self, job_id, context):
        self._deny_if_untrusted(context)
        with self.owner.connection(readonly=True) as conn:
            self._principal(conn, context)
            row = conn.execute('''SELECT a.attempt_id,a.worker_id,a.assignment_epoch,a.lease_generation,
                a.source_base_sha,a.checkpoint_sha,a.artifact_ref,j.source_repo,j.approved_scope_hash,
                (SELECT snapshot_hash FROM recovery_checkpoints c WHERE c.attempt_id=a.attempt_id
                 ORDER BY c.created_at DESC,c.checkpoint_id DESC LIMIT 1)
                FROM attempts a JOIN jobs j ON j.job_id=a.job_id WHERE a.job_id=? AND a.principal_id=?
                AND a.assignment_epoch=j.assignment_epoch ORDER BY a.started_at DESC,a.attempt_id DESC LIMIT 1''',
                (job_id, context.principal_id)).fetchone()
            if row is None:
                raise Denied('ACCESS_REQUIRED')
            names = ('attempt_id', 'worker_id', 'assignment_epoch', 'lease_generation', 'source_base_sha',
                     'checkpoint_sha256', 'artifact_ref', 'source_repo', 'approved_scope_sha256', 'snapshot_hash')
            return dict(zip(names, row), job_id=job_id, authority='SHADOW_ONLY')
