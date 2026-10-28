"""Transactional PostgreSQL implementation of DB-CONTRACT/1.

Trusted authentication belongs to WG-D. No caller-supplied command fields authenticate
an identity. Only an independently authenticated gateway may create GatewayContext.
All writes remain local/shadow until signed owner-gated cutover outside this module.
"""
from __future__ import annotations

import hashlib
import json
from .publication import PublicationAuthorityMixin
from .authority_contract import (
    COMMAND_VALIDATOR, RESULT_VALIDATOR, EVENT_VALIDATOR, RESUME_VALIDATOR,
    GatewayContext, StorageError, Denied, _canonical, _digest, _utc, _fresh,
    make_result, deny_if_untrusted,
)

class PostgresStore(PublicationAuthorityMixin):
    """Connection factory must return independent psycopg3 connections, autocommit=True.

    Supply e.g. `lambda: psycopg.connect(dsn, autocommit=True)`; no secrets stored.
    A distinct connection is opened per command so the row locks are real.
    """
    def __init__(self, connect):
        self._connect = connect

    _deny_if_untrusted = staticmethod(deny_if_untrusted)

    def _execute(self, command: dict, context: GatewayContext) -> dict:
        """Validate closed WG-A command and execute one ACID transaction.

        Denials do not commit command_result identities, so transient constraints can
        later become authorized. Successful commands are keyed by exact payload SHA.
        """
        try:
            COMMAND_VALIDATOR.validate(command)
        except Exception:
            return self._result(command.get('command_id', 'invalid'), 'DENIED','MALFORMED_REQUEST')
        try:
            self._deny_if_untrusted(context)
        except Denied as exc:
            return self._result(command['command_id'],'DENIED',exc.reason)
        payload_hash = _digest(command)
        with self._connect() as conn:
            with conn.transaction():
                # A transaction-scoped advisory lock serializes same-key retries; no side effects
                # happen before the idempotency check.
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 2026))', (command['command_id'],))
                previous = conn.execute('SELECT payload_sha256,result_json FROM nomad_v16.command_results WHERE command_id=%s', (command['command_id'],)).fetchone()
                if previous:
                    if previous[0].strip() != payload_hash:
                        return self._result(command['command_id'],'DENIED','IDEMPOTENCY_CONFLICT')
                    saved = previous[1] if isinstance(previous[1],dict) else json.loads(previous[1])
                    saved = dict(saved, result='DUPLICATE',commit_sequence=None)
                    RESULT_VALIDATOR.validate(saved)
                    return saved
                try:
                    data = self._dispatch(conn,command,context)
                except Denied as exc:
                    # Raising exits the outer transaction without a commit; no partial writes survive.
                    raise _CommandDenied(exc.reason)
                event_id = data.get('event_id') or self._journal_internal(conn,command)
                seq = conn.execute('INSERT INTO nomad_v16.command_results(command_id,payload_sha256,result_json) VALUES(%s,%s,%s::jsonb) RETURNING commit_sequence',
                                   (command['command_id'],payload_hash,'{}')).fetchone()[0]
                result = self._result(command['command_id'],'COMMITTED',None, seq, data.get('lease_generation'),event_id,data.get('checkpoint_sha256'))
                conn.execute('UPDATE nomad_v16.command_results SET result_json=%s::jsonb WHERE command_id=%s', (_canonical(result),command['command_id']))
                return result

    def execute(self, command: dict, context: GatewayContext) -> dict:
        """Stable denial-returning external facade (rolls back before reporting denial)."""
        try:
            return self._execute(command,context)
        except _CommandDenied as exc:
            return self._result(command.get('command_id','invalid'),'DENIED',exc.reason)

    command = execute

    _result = staticmethod(make_result)

    @staticmethod
    def _journal_internal(conn, cmd):
        event_id = _fresh('evt_')
        envelope = dict(schema_id='DB-CONTRACT/EVENT/1',event_id=event_id,
                        producer='POSTGRES_SHADOW',transport_delivery_id=f"cmd:{cmd['command_id']}",
                        job_id=cmd['job_id'],worker_id=cmd['worker_id'],attempt_id=cmd['attempt_id'],
                        assignment_epoch=cmd['assignment_epoch'], event_kind={'CLAIM':'STARTED','CHECKPOINT':cmd['payload'].get('state','CHECKPOINT').replace('CHECKPOINTED','CHECKPOINT'), 'ACK':'ACK', 'REASSIGN':'HANDOFF_READY', 'RESERVE_PUBLICATION':'HANDOFF_READY', 'RECONCILE_PUBLICATION':'ACK', 'OUTBOX_SCHEDULE':'HANDOFF_READY', 'OUTBOX_ACK':'ACK'}[cmd['command_kind']],
                        causation_id=None,correlation_id=cmd['command_id'],payload_sha256=_digest(cmd),
                        immutable_evidence_ref='urn:nomad:db-command:'+hashlib.sha256(cmd['command_id'].encode()).hexdigest(),received_at=_utc(), delivery_authority='ARCHIVE_ONLY')
        # Internal journal format need not masquerade as provider event. Its transport
        # identity remains distinct from any historical V1 observation.
        EVENT_VALIDATOR.validate(envelope)
        conn.execute('INSERT INTO nomad_v16.event_journal(event_id,producer,transport_delivery_id,partition_key,payload_sha256,canonical_event_json) VALUES(%s,%s,%s,%s,%s,%s::jsonb)',
                     (event_id,'POSTGRES_SHADOW',f"cmd:{cmd['command_id']}",cmd['job_id'],_digest(cmd),_canonical(envelope)))
        return event_id

    @staticmethod
    def _authority(conn,cmd,ctx,operation, *, lock_job=True, require_attempt=True):
        lock_sql=' FOR UPDATE SKIP LOCKED' if lock_job else ''
        fields = conn.execute('SELECT job_id,state,authority_revision,source_repo,source_base_sha,approved_scope_hash,resource_key,assignment_epoch FROM nomad_v16.jobs WHERE job_id=%s'+lock_sql, (cmd['job_id'],)).fetchone()
        if not fields:
            raise Denied('RETRYABLE_BLOCKED' if lock_job else 'ACCESS_REQUIRED')
        job_id,state,revision,repo,base,scope,resource,epoch=fields
        if state in ('CANCELLED','DONE','BLOCKED') or revision!=cmd['authority_revision']:
            raise Denied('AUTHORITY_REVISION_MISMATCH' if revision!=cmd['authority_revision'] else 'ACCESS_REQUIRED')
        if epoch!=cmd['assignment_epoch']:
            raise Denied('STALE_ASSIGNMENT')
        active=conn.execute("SELECT 1 FROM nomad_v16.worker_assignments WHERE job_id=%s AND worker_id=%s AND assignment_epoch=%s AND status='ACTIVE' AND revoked_at IS NULL",
                            (job_id,cmd['worker_id'],epoch)).fetchone()
        if not active:
            raise Denied('STALE_ASSIGNMENT')
        principal=conn.execute("SELECT status FROM nomad_v16.principals WHERE principal_id=%s",(ctx.principal_id,)).fetchone()
        if not principal or principal[0]!='ACTIVE':
            raise Denied('IDENTITY_UNVERIFIED')
        # Source of truth for permission/approval is live DB, not command payload.
        permit=conn.execute("""SELECT g.grant_id,g.expires_at,g.revoked_at,g.assignment_epoch,g.source_base_sha,
                          d.revoked_at,d.expires_at,d.approved_scope_hash,d.authority_revision,d.decision_id
            FROM nomad_v16.grants g JOIN nomad_v16.decisions d ON d.decision_id=g.issuer_approval_ref
            JOIN nomad_v16.principals issuer ON issuer.principal_id=d.authority_principal_id
            WHERE g.principal_id=%s AND g.job_id=%s AND g.worker_id=%s AND g.repo_id=%s
              AND g.operation=%s AND g.authority_revision=%s AND g.approved_scope_hash=%s
              AND d.job_id=g.job_id AND d.approved_scope_hash=g.approved_scope_hash
              AND d.action=g.operation AND issuer.status='ACTIVE'
            ORDER BY g.expires_at DESC LIMIT 1""",
            (ctx.principal_id,job_id,cmd['worker_id'],repo,operation,revision,scope)).fetchone()
        if not permit:
            raise Denied('ACCESS_REQUIRED')
        grant_id,until,revoked,grant_epoch,grant_base,decision_revoked,decision_expires,decision_scope,decision_revision,decision_id=permit
        now=conn.execute('SELECT clock_timestamp()').fetchone()[0]
        if revoked or decision_revoked:
            raise Denied('REVOKED_GRANT')
        if until<=now or (decision_expires and decision_expires<=now):
            raise Denied('EXPIRED_GRANT')
        if grant_epoch!=epoch:
            raise Denied('STALE_ASSIGNMENT')
        if grant_base!=base or decision_revision!=revision or decision_scope!=scope:
            raise Denied('REBASE_REQUIRED')
        if require_attempt:
            attempt=conn.execute('SELECT status,lease_generation FROM nomad_v16.attempts WHERE attempt_id=%s AND job_id=%s AND worker_id=%s AND assignment_epoch=%s FOR UPDATE',
                                 (cmd['attempt_id'],job_id,cmd['worker_id'],epoch)).fetchone()
            if not attempt or attempt[0] in ('STOPPED','SUBMITTED'):
                raise Denied('STALE_LEASE')
            lease=conn.execute('SELECT lease_generation,holder_attempt_id,expires_at FROM nomad_v16.resource_leases WHERE repo_id=%s AND resource_key=%s FOR UPDATE',
                               (repo,resource)).fetchone()
            if not lease or lease[1]!=cmd['attempt_id'] or lease[0]!=attempt[1] or lease[2]<=now:
                raise Denied('STALE_LEASE')
        return dict(job_id=job_id,repo=repo,base=base,scope=scope,resource=resource,epoch=epoch,grant_id=grant_id,decision_id=decision_id)

    def _dispatch(self,conn,cmd,ctx):
        kind=cmd['command_kind']
        if kind=='CLAIM': return self._claim(conn,cmd,ctx)
        if kind=='REASSIGN': return self._reassign(conn,cmd,ctx)
        if kind=='CHECKPOINT': return self._checkpoint(conn,cmd,ctx)
        if kind=='ACK': return self._ack(conn,cmd,ctx)
        if kind=='RESERVE_PUBLICATION': return self._reserve(conn,cmd,ctx)
        if kind=='RECONCILE_PUBLICATION': return self._reconcile(conn,cmd,ctx)
        if kind=='OUTBOX_SCHEDULE': return self._schedule(conn,cmd,ctx)
        if kind=='OUTBOX_ACK': return self._outbox_ack(conn,cmd,ctx)
        raise Denied('MALFORMED_REQUEST')

    def _claim(self,conn,cmd,ctx):
        p=cmd['payload']; j=self._authority(conn,cmd,ctx,'CLAIM',require_attempt=False)
        if p['source_base_sha']!=j['base'] or not ctx.verified_source_base:
            raise Denied('REBASE_REQUIRED')
        if p['resource_key']!=j['resource'] or p['grant_id']!=j['grant_id'] or p['owner_decision_ref']!=j['decision_id']:
            raise Denied('INVALID_SCOPE')
        dependencies=conn.execute("""SELECT 1 FROM nomad_v16.job_dependencies d JOIN nomad_v16.jobs j ON j.job_id=d.prerequisite_job_id
                                    WHERE d.job_id=%s AND j.state<>'DONE' LIMIT 1""",(cmd['job_id'],)).fetchone()
        if dependencies: raise Denied('RETRYABLE_BLOCKED')
        # No automatic paid spending. Any approved nonzero budget is handled by owner.
        budget=conn.execute("SELECT 1 FROM nomad_v16.budget_reservations WHERE job_id=%s AND amount>0 AND status<>'APPROVED' LIMIT 1",(cmd['job_id'],)).fetchone()
        if budget: raise Denied('BUDGET_DENIED')
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 1777))',(cmd['attempt_id'],))
        if conn.execute('SELECT 1 FROM nomad_v16.attempts WHERE attempt_id=%s',(cmd['attempt_id'],)).fetchone():
            raise Denied('IDEMPOTENCY_CONFLICT')
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 1976))',(j['repo']+'|'+j['resource'],))
        lease=conn.execute('SELECT lease_generation,holder_attempt_id,expires_at FROM nomad_v16.resource_leases WHERE repo_id=%s AND resource_key=%s FOR UPDATE',
                           (j['repo'],j['resource'])).fetchone()
        now=conn.execute('SELECT clock_timestamp()').fetchone()[0]
        expiration=datetime.fromisoformat(p['lease_expires_at'].replace('Z','+00:00'))
        if expiration<=now: raise Denied('STALE_LEASE')
        if lease:
            old_gen,holder,expiry=lease
            if old_gen!=p['expected_lease_generation']: raise Denied('STALE_LEASE')
            if holder and expiry>now: raise Denied('RETRYABLE_BLOCKED')
            generation=old_gen+1
        else:
            if p['expected_lease_generation']!=1: raise Denied('STALE_LEASE')
            generation=1
        conn.execute("INSERT INTO nomad_v16.attempts(attempt_id,job_id,worker_id,principal_id,assignment_epoch,status,source_base_sha,lease_generation) VALUES(%s,%s,%s,%s,%s,'STARTED',%s,%s)",
                     (cmd['attempt_id'],cmd['job_id'],cmd['worker_id'],ctx.principal_id,cmd['assignment_epoch'],j['base'],generation))
        conn.execute("""INSERT INTO nomad_v16.resource_leases(repo_id,resource_key,lease_generation,holder_attempt_id,expires_at,baseline_sha)
                     VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(repo_id,resource_key)
                     DO UPDATE SET lease_generation=EXCLUDED.lease_generation,holder_attempt_id=EXCLUDED.holder_attempt_id,
                       expires_at=EXCLUDED.expires_at,baseline_sha=EXCLUDED.baseline_sha""",
                     (j['repo'],j['resource'],generation,cmd['attempt_id'],expiration,j['base']))
        conn.execute("UPDATE nomad_v16.jobs SET state='CLAIMED',version=version+1 WHERE job_id=%s",(cmd['job_id'],))
        return dict(lease_generation=generation)

    def _checkpoint(self,conn,cmd,ctx):
        j=self._authority(conn,cmd,ctx,'CHECKPOINT');p=cmd['payload']
        if p['source_base_sha']!=j['base']: raise Denied('REBASE_REQUIRED')
        conn.execute("""UPDATE nomad_v16.attempts SET status=%s, checkpoint_sha=%s, artifact_ref=%s,
                     elapsed_observed_seconds=%s,resume_required=%s,
                     ended_at=CASE WHEN %s IN ('YIELDED','SUBMITTED','STOPPED') THEN clock_timestamp() ELSE NULL END
                     WHERE attempt_id=%s""",(p['state'],p['checkpoint_sha256'],p['artifact_ref'],p['elapsed_observed_seconds'],p['resume_required'],p['state'],cmd['attempt_id']))
        if p['state'] in ('YIELDED','SUBMITTED','STOPPED'):
            conn.execute('UPDATE nomad_v16.resource_leases SET holder_attempt_id=NULL WHERE holder_attempt_id=%s',(cmd['attempt_id'],))
            conn.execute('UPDATE nomad_v16.jobs SET state=%s,version=version+1 WHERE job_id=%s',
                         ('YIELDED' if p['state']=='YIELDED' else 'SUBMITTED' if p['state']=='SUBMITTED' else 'READY',cmd['job_id']))
        conn.execute('INSERT INTO nomad_v16.recovery_checkpoints(checkpoint_id,job_id,attempt_id,source_ref,snapshot_hash) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(attempt_id,snapshot_hash) DO NOTHING',
                     (_fresh('cp_'),cmd['job_id'],cmd['attempt_id'],p['artifact_ref'],p['checkpoint_sha256']))
        return dict(checkpoint_sha256=p['checkpoint_sha256'])

    def _reassign(self,conn,cmd,ctx):
        j=self._authority(conn,cmd,ctx,'REASSIGN',require_attempt=False);p=cmd['payload']
        # Old worker remains the actor; reassign authority must be an explicit owner decision.
        decision=conn.execute("""SELECT 1 FROM nomad_v16.decisions d JOIN nomad_v16.principals owner ON owner.principal_id=d.authority_principal_id
                        WHERE d.decision_id=%s AND d.job_id=%s AND d.action='REASSIGN' AND d.revoked_at IS NULL
                        AND d.authority_revision=%s AND d.approved_scope_hash=%s AND owner.status='ACTIVE'
                        AND (d.expires_at IS NULL OR d.expires_at>clock_timestamp())""",
                        (p['revocation_decision_ref'],cmd['job_id'],cmd['authority_revision'],j['scope'])).fetchone()
        if not decision: raise Denied('OWNER_APPROVAL_REQUIRED')
        if p['new_assignment_epoch']!=j['epoch']+1: raise Denied('STALE_ASSIGNMENT')
        lease=conn.execute('SELECT lease_generation FROM nomad_v16.resource_leases WHERE repo_id=%s AND resource_key=%s FOR UPDATE',
                           (j['repo'],j['resource'])).fetchone()
        if not lease or p['new_lease_generation']!=lease[0]+1: raise Denied('STALE_LEASE')
        if p['transfer_checkpoint_sha256'] is not None:
            found=conn.execute('SELECT 1 FROM nomad_v16.attempts WHERE job_id=%s AND checkpoint_sha=%s',
                               (cmd['job_id'],p['transfer_checkpoint_sha256'])).fetchone()
            if not found: raise Denied('SOURCE_SHA_MISMATCH')
        conn.execute("UPDATE nomad_v16.worker_assignments SET status='REVOKED',revoked_at=clock_timestamp() WHERE job_id=%s AND status='ACTIVE'",(cmd['job_id'],))
        conn.execute("INSERT INTO nomad_v16.worker_assignments(job_id,worker_id,assignment_epoch,status) VALUES(%s,%s,%s,'ACTIVE')",
                     (cmd['job_id'],p['new_worker_id'],p['new_assignment_epoch']))
        conn.execute('UPDATE nomad_v16.jobs SET assignment_epoch=%s,state=\'READY\',version=version+1 WHERE job_id=%s',(p['new_assignment_epoch'],cmd['job_id']))
        conn.execute('UPDATE nomad_v16.resource_leases SET holder_attempt_id=NULL,lease_generation=%s,expires_at=clock_timestamp() WHERE repo_id=%s AND resource_key=%s',
                     (p['new_lease_generation'],j['repo'],j['resource']))
        return dict(lease_generation=p['new_lease_generation'],checkpoint_sha256=p['transfer_checkpoint_sha256'])

    def _ack(self,conn,cmd,ctx):
        self._authority(conn,cmd,ctx,'ACK');p=cmd['payload']
        if p['recipient_worker_id']!=cmd['worker_id'] or p['delivery_assignment_epoch']!=cmd['assignment_epoch']:
            raise Denied('STALE_ASSIGNMENT')
        event=conn.execute('SELECT 1 FROM nomad_v16.event_journal WHERE event_id=%s AND partition_key=%s',(p['event_id'],cmd['job_id'])).fetchone()
        if not event: raise Denied('ACCESS_REQUIRED')
        conn.execute('INSERT INTO nomad_v16.delivery_effects(event_id,consumer_id,assignment_epoch,effect_name) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     (p['event_id'],p['recipient_worker_id'],p['delivery_assignment_epoch'],p['effect_name']))
        conn.execute('INSERT INTO nomad_v16.inbox_cursors(job_id,worker_id,assignment_epoch,last_event_id) VALUES(%s,%s,%s,%s) ON CONFLICT(job_id,worker_id,assignment_epoch) DO UPDATE SET last_event_id=EXCLUDED.last_event_id,updated_at=clock_timestamp()',
                     (cmd['job_id'],cmd['worker_id'],cmd['assignment_epoch'],p['event_id']))
        return dict(event_id=p['event_id'])

    def _reserve(self,conn,cmd,ctx):
        """Frozen DB command omits patch/tree/revision/QA data: deny publication.

        The fully validated WG-D publication path is reserve_gateway_intent,
        which atomically writes the exact immutable intent under live DB authority.
        """
        raise Denied('GATEWAY_UNAVAILABLE')

    def _reconcile(self,conn,cmd,ctx):
        """Legacy partial publication rows cannot prove exact external effects."""
        raise Denied('GATEWAY_UNAVAILABLE')

    def _schedule(self,conn,cmd,ctx):
        self._authority(conn,cmd,ctx,'OUTBOX_SCHEDULE');p=cmd['payload']
        found=conn.execute('SELECT payload_sha256 FROM nomad_v16.event_journal WHERE event_id=%s AND partition_key=%s',(p['event_id'],cmd['job_id'])).fetchone()
        if not found or found[0].strip()!=p['payload_sha256']: raise Denied('SOURCE_SHA_MISMATCH')
        # Uniqueness is on (event,destination,kind), not client command id alone.
        existing=conn.execute('SELECT payload_sha256 FROM nomad_v16.outbox WHERE event_id=%s AND destination=%s AND kind=%s',
                              (p['event_id'],p['destination'],p['kind'])).fetchone()
        if existing and existing[0].strip()!=p['payload_sha256']: raise Denied('IDEMPOTENCY_CONFLICT')
        conn.execute("""INSERT INTO nomad_v16.outbox(outbox_id,event_id,kind,destination,status,payload_sha256)
                      VALUES(%s,%s,%s,%s,'PREPARED',%s) ON CONFLICT(event_id,destination,kind) DO NOTHING""",
                     (_fresh('out_'),p['event_id'],p['kind'],p['destination'],p['payload_sha256']))
        return dict(event_id=p['event_id'])

    def _outbox_ack(self,conn,cmd,ctx):
        self._authority(conn,cmd,ctx,'OUTBOX_ACK');p=cmd['payload']
        if not ctx.verified_external_receipt: raise Denied('ACCESS_REQUIRED')
        record=conn.execute("SELECT o.payload_sha256,o.status,o.provider_ref FROM nomad_v16.outbox o JOIN nomad_v16.event_journal e ON e.event_id=o.event_id WHERE o.outbox_id=%s AND e.partition_key=%s AND o.kind NOT IN ('GITHUB_EFFECT','DRIVE_EFFECT') FOR UPDATE OF o",(p['outbox_id'],cmd['job_id'])).fetchone()
        if not record: raise Denied('ACCESS_REQUIRED')
        if p['effect_sha256']!=record[0].strip(): raise Denied('SOURCE_SHA_MISMATCH')
        if record[1] not in ('IN_FLIGHT','RECONCILE_REQUIRED','DELIVERED'): raise Denied('RETRYABLE_BLOCKED')
        if record[1]=='DELIVERED' and record[2]!=p['provider_ref']: raise Denied('IDEMPOTENCY_CONFLICT')
        conn.execute("UPDATE nomad_v16.outbox SET status='DELIVERED',provider_ref=%s,provider_effect_sha256=%s,claim_token=NULL,claim_expires_at=NULL WHERE outbox_id=%s",
                     (p['provider_ref'],p['effect_sha256'],p['outbox_id']))
        return {}

    def record_event(self, envelope: dict, context: GatewayContext) -> str:
        """Immutable idempotent transport ingestion; historical envelopes remain ARCHIVE_ONLY."""
        self._deny_if_untrusted(context)
        EVENT_VALIDATOR.validate(envelope)
        with self._connect() as conn, conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 2026))',(envelope['producer']+'|'+envelope['transport_delivery_id'],))
            found=conn.execute('SELECT event_id,payload_sha256 FROM nomad_v16.event_journal WHERE producer=%s AND transport_delivery_id=%s',
                               (envelope['producer'],envelope['transport_delivery_id'])).fetchone()
            if found:
                if found[0]!=envelope['event_id'] or found[1].strip()!=envelope['payload_sha256']: raise Denied('IDEMPOTENCY_CONFLICT')
                return found[0]
            if conn.execute('SELECT 1 FROM nomad_v16.event_journal WHERE event_id=%s',(envelope['event_id'],)).fetchone():
                raise Denied('IDEMPOTENCY_CONFLICT')
            conn.execute('INSERT INTO nomad_v16.event_journal(event_id,producer,transport_delivery_id,partition_key,payload_sha256,canonical_event_json,immutable_evidence_ref) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s)',
                         (envelope['event_id'],envelope['producer'],envelope['transport_delivery_id'],envelope['job_id'],envelope['payload_sha256'],_canonical(envelope),envelope['immutable_evidence_ref']))
            return envelope['event_id']

    def reserve_outbox_delivery(self, context: GatewayContext, max_items=20, max_attempts=5, lease_seconds=90, *, shadow_test: bool = False):
        """Short SQL delivery reservation; never invokes external providers in the transaction."""
        self._deny_if_untrusted(context)
        if not (0 < max_items <= 100 and 0 < max_attempts <= 20 and 1 <= lease_seconds <= 600):
            raise ValueError('bounded delivery parameters required')
        with self._connect() as conn, conn.transaction():
            mode=conn.execute('SELECT mode FROM nomad_v16.authority_mode WHERE singleton=true').fetchone()
            if not mode or (mode[0]=='SHADOW' and not shadow_test):
                raise Denied('CUTOVER_NOT_APPROVED')
            # Timed-out delivery is UNKNOWN until authenticated provider readback.
            # Never resubmit an unknown effect just because the delivery lease expired.
            conn.execute("""UPDATE nomad_v16.outbox SET status='RECONCILE_REQUIRED',
                 claim_token=NULL, claim_expires_at=NULL
                 WHERE status='IN_FLIGHT' AND claim_expires_at<clock_timestamp()""")
            conn.execute("""UPDATE nomad_v16.outbox SET status='DEAD_LETTER',dead_letter_reason='RETRY_LIMIT'
                 WHERE status='RETRY' AND attempts >=%s""",(max_attempts,))
            rows=conn.execute("""SELECT outbox_id,event_id,kind,destination,payload_sha256,attempts
                 FROM nomad_v16.outbox WHERE kind NOT IN ('GITHUB_EFFECT','DRIVE_EFFECT') AND status IN ('PREPARED','RETRY') AND next_retry_at<=clock_timestamp()
                 ORDER BY next_retry_at,created_at FOR UPDATE SKIP LOCKED LIMIT %s""",(max_items,)).fetchall()
            result=[]
            for item in rows:
                token=_fresh('claim_')
                conn.execute("""UPDATE nomad_v16.outbox SET status='IN_FLIGHT',attempts=attempts+1,
                     claim_token=%s,claim_expires_at=clock_timestamp()+(%s * interval '1 second') WHERE outbox_id=%s""",
                     (token,lease_seconds,item[0]))
                result.append(dict(outbox_id=item[0],event_id=item[1],kind=item[2],destination=item[3],payload_sha256=item[4].strip(),attempts=item[5]+1,claim_token=token))
            return result

    def mark_delivery_retry(self,outbox_id,claim_token,reason,context: GatewayContext,max_attempts=5):
        """Only holder of the claim may record an unknown delivery for readback."""
        self._deny_if_untrusted(context)
        with self._connect() as conn, conn.transaction():
            row=conn.execute("SELECT attempts,status FROM nomad_v16.outbox WHERE outbox_id=%s AND claim_token=%s AND kind NOT IN ('GITHUB_EFFECT','DRIVE_EFFECT') FOR UPDATE",(outbox_id,claim_token)).fetchone()
            if not row or row[1]!='IN_FLIGHT': raise Denied('STALE_LEASE')
            # A provider error/timeout has unknown disposition. Reconciliation comes first.
            conn.execute("""UPDATE nomad_v16.outbox SET status='RECONCILE_REQUIRED',
                   claim_token=NULL,claim_expires_at=NULL,dead_letter_reason=%s WHERE outbox_id=%s""",
                   (reason[:200],outbox_id))
            return 'RECONCILE_REQUIRED'


    def reconcile_outbox(self, outbox_id, readback_state, observed_sha256, provider_ref,
                         context: GatewayContext, *, max_attempts=5):
        """External authenticated READBACK BEFORE RETRY. Never invokes provider I/O.

        PRESENT + matching digest => DELIVERED; ABSENT => retry with same event key;
        CONFLICT => BLOCKED. All provider observations must be gateway-verified.
        """
        self._deny_if_untrusted(context)
        if not context.provider_readback_verified: raise Denied('ACCESS_REQUIRED')
        if readback_state not in ('PRESENT','ABSENT','CONFLICT'): raise ValueError('unsupported readback')
        with self._connect() as conn,conn.transaction():
            row=conn.execute("SELECT payload_sha256,status,attempts,provider_ref FROM nomad_v16.outbox WHERE outbox_id=%s AND kind NOT IN ('GITHUB_EFFECT','DRIVE_EFFECT') FOR UPDATE",(outbox_id,)).fetchone()
            if not row: raise Denied('ACCESS_REQUIRED')
            digest,status,attempts,previous_ref=row
            if status=='DELIVERED':
                if readback_state!='PRESENT' or observed_sha256!=digest.strip() or provider_ref!=previous_ref:
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return status
            if status not in ('RECONCILE_REQUIRED','IN_FLIGHT'):
                raise Denied('RETRYABLE_BLOCKED')
            if readback_state=='PRESENT':
                if observed_sha256!=digest.strip() or not provider_ref:
                    new_status='BLOCKED'
                else:
                    new_status='DELIVERED'
            elif readback_state=='ABSENT':
                new_status='DEAD_LETTER' if attempts>=max_attempts else 'RETRY'
            else:
                new_status='BLOCKED'
            conn.execute("""UPDATE nomad_v16.outbox SET status=%s,
                    provider_ref=CASE WHEN %s='DELIVERED' THEN %s ELSE provider_ref END,
                    provider_effect_sha256=CASE WHEN %s='DELIVERED' THEN %s ELSE provider_effect_sha256 END,
                    dead_letter_reason=CASE WHEN %s='BLOCKED' THEN 'READBACK_CONFLICT'
                             WHEN %s='DEAD_LETTER' THEN 'RETRY_LIMIT' ELSE NULL END,
                    claim_token=NULL,claim_expires_at=NULL,
                    next_retry_at=clock_timestamp()+(%s*interval '1 second') WHERE outbox_id=%s""",
                    (new_status,new_status,provider_ref,new_status,observed_sha256,
                     new_status,new_status,min(3600,2**min(attempts,11)),outbox_id))
            return new_status

    def shadow_import(self, import_id, source_kind, source_ref, source_revision, source_json, context):
        """Copy immutable source snapshots. Do not turn imported claims into authority."""
        self._deny_if_untrusted(context)
        if source_kind not in ('GITHUB','DRIVE','LEDGER'): raise ValueError('unknown source kind')
        digest=_digest(source_json)
        with self._connect() as conn,conn.transaction():
            mode=conn.execute('SELECT mode FROM nomad_v16.authority_mode WHERE singleton=true FOR UPDATE').fetchone()
            if not mode or mode[0]!='SHADOW': raise Denied('CUTOVER_NOT_APPROVED')
            saved=conn.execute('SELECT content_sha256 FROM nomad_v16.shadow_imports WHERE source_kind=%s AND source_ref=%s AND source_revision=%s',
                               (source_kind,source_ref,source_revision)).fetchone()
            if saved:
                if saved[0].strip()!=digest: raise Denied('IDEMPOTENCY_CONFLICT')
                return digest
            inserted=conn.execute('INSERT INTO nomad_v16.shadow_imports(import_id,source_kind,source_ref,source_revision,content_sha256,source_json) VALUES(%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING RETURNING import_id',
                         (import_id,source_kind,source_ref,source_revision,digest,_canonical(source_json))).fetchone()
            if not inserted:
                saved=conn.execute('SELECT content_sha256 FROM nomad_v16.shadow_imports WHERE source_kind=%s AND source_ref=%s AND source_revision=%s',
                                   (source_kind,source_ref,source_revision)).fetchone()
                if not saved or saved[0].strip()!=digest: raise Denied('IDEMPOTENCY_CONFLICT')
            return digest

    def reconcile_shadow(self, source_kind, source_ref, source_revision, expected_digest, context):
        """Compare frozen authoritative readback; never automatically activates writers."""
        self._deny_if_untrusted(context)
        if not context.provider_readback_verified: raise Denied('ACCESS_REQUIRED')
        with self._connect() as conn,conn.transaction():
            row=conn.execute('SELECT content_sha256 FROM nomad_v16.shadow_imports WHERE source_kind=%s AND source_ref=%s AND source_revision=%s FOR UPDATE',
                             (source_kind,source_ref,source_revision)).fetchone()
            if not row: raise Denied('ACCESS_REQUIRED')
            status='MATCH' if row[0].strip()==expected_digest else 'MISMATCH'
            conn.execute('UPDATE nomad_v16.shadow_imports SET reconciliation_status=%s WHERE source_kind=%s AND source_ref=%s AND source_revision=%s',
                         (status,source_kind,source_ref,source_revision))
            return status

    def save_resume_capsule(self, capsule: dict, context: GatewayContext):
        """Persist a WG-A valid worker observation at its exact checkpoint.

        The capsule is not trusted owner approval or host attestation. It may be
        recorded after YIELD, before any reassignment; old epochs cannot overwrite
        a new assignment's recovery state.
        """
        self._deny_if_untrusted(context)
        RESUME_VALIDATOR.validate(capsule)
        with self._connect() as conn,conn.transaction():
            row=conn.execute("""SELECT a.worker_id,a.assignment_epoch,a.lease_generation,a.source_base_sha,a.checkpoint_sha,
                       a.principal_id,j.source_repo,j.assignment_epoch
                       FROM nomad_v16.attempts a JOIN nomad_v16.jobs j ON j.job_id=a.job_id
                       WHERE a.job_id=%s AND a.attempt_id=%s FOR UPDATE OF a""",
                       (capsule['job_id'],capsule['attempt_id'])).fetchone()
            if not row or row[5]!=context.principal_id: raise Denied('ACCESS_REQUIRED')
            if row[7]!=capsule['assignment_epoch'] or row[1]!=capsule['assignment_epoch']:
                raise Denied('STALE_ASSIGNMENT')
            if (row[0]!=capsule['worker_id'] or row[2]!=capsule['lease_generation'] or
                row[3]!=capsule['source_base_sha'] or row[4]!=capsule['checkpoint_sha256'] or
                row[6]!=capsule['source_repo']):
                raise Denied('SOURCE_SHA_MISMATCH')
            raw=_canonical(capsule)
            existing=conn.execute("SELECT capsule_json FROM nomad_v16.resume_capsules WHERE job_id=%s AND attempt_id=%s AND checkpoint_sha256=%s",
                                  (capsule['job_id'],capsule['attempt_id'],capsule['checkpoint_sha256'])).fetchone()
            if existing:
                prior=existing[0] if isinstance(existing[0],dict) else json.loads(existing[0])
                if _canonical(prior)!=raw: raise Denied('IDEMPOTENCY_CONFLICT')
                return _digest(capsule)
            conn.execute("INSERT INTO nomad_v16.resume_capsules(job_id,attempt_id,checkpoint_sha256,capsule_json) VALUES(%s,%s,%s,%s::jsonb)",
                         (capsule['job_id'],capsule['attempt_id'],capsule['checkpoint_sha256'],raw))
            return _digest(capsule)

    def load_resume_capsule(self, job_id, context: GatewayContext):
        """Return latest schema-valid stored capsule, never invent missing fields."""
        self._deny_if_untrusted(context)
        with self._connect() as conn,conn.transaction():
            row=conn.execute("""SELECT r.capsule_json FROM nomad_v16.resume_capsules r
                   JOIN nomad_v16.attempts a ON a.attempt_id=r.attempt_id
                   WHERE r.job_id=%s ORDER BY r.captured_at DESC,a.started_at DESC LIMIT 1""",(job_id,)).fetchone()
            if not row: return None
            capsule=row[0] if isinstance(row[0],dict) else json.loads(row[0])
            RESUME_VALIDATOR.validate(capsule)
            return capsule

    def resume_capsule(self, job_id, context):
        """Exact last checkpoint/fence; unfilled optional fields remain for WG-F."""
        self._deny_if_untrusted(context)
        with self._connect() as conn,conn.transaction():
            row=conn.execute("""SELECT a.attempt_id,a.worker_id,a.assignment_epoch,a.lease_generation,a.source_base_sha,
                        a.checkpoint_sha,a.artifact_ref,j.source_repo,j.approved_scope_hash,rc.snapshot_hash
               FROM nomad_v16.attempts a JOIN nomad_v16.jobs j ON j.job_id=a.job_id
               LEFT JOIN LATERAL (SELECT snapshot_hash FROM nomad_v16.recovery_checkpoints WHERE attempt_id=a.attempt_id ORDER BY created_at DESC LIMIT 1) rc ON true
               WHERE a.job_id=%s ORDER BY a.started_at DESC,a.attempt_id DESC LIMIT 1""",(job_id,)).fetchone()
            if not row: raise Denied('ACCESS_REQUIRED')
            return dict(job_id=job_id,attempt_id=row[0],worker_id=row[1],assignment_epoch=row[2],
                        lease_generation=row[3],source_base_sha=row[4],checkpoint_sha256=row[5],artifact_ref=row[6],
                        source_repo=row[7],approved_scope_sha256=row[8],snapshot_hash=row[9],
                        authority='SHADOW_ONLY')

class _CommandDenied(Exception):
    def __init__(self,reason):
        self.reason=reason
