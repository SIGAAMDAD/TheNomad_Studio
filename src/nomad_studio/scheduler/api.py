"""R9 SchedulingPort, no department authorization and no legacy command envelopes.

All database mutations are CAB-C-owned and must execute under a P2-injected
single-writer table-limited transaction. Every provider/evidence verifier runs
outside the transaction. External effect uncertainty is a recovery block.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from nomad_studio.production.workgraph.core import (Decision, Refused, Result,
    critical_path, digest, hash_record, ident, integer, refused, require, validate_task)
from nomad_studio.shift.api import active_lease, begin_attempt, close_attempt


class SchedulingService:
    def __init__(self, writer: Any, *, clock: Callable[[],int],
                 allocation_guard: Callable[[str,int,Mapping[str,Any]], Decision] | None = None,
                 placement_guard: Callable[[Mapping[str,Any],str,Decision], Decision] | None = None,
                 checkpoint_guard: Callable[[Mapping[str,Any]], Decision] | None = None,
                 recovery_guard: Callable[[Mapping[str,Any]], Decision] | None = None,
                 completion_guard: Callable[[Mapping[str,Any],Mapping[str,Any]], Decision] | None = None,
                 ttl_us: int = 30_000_000, retry_limit: int = 2,
                 retry_base_us: int = 1_000_000):
        integer(ttl_us,1); integer(retry_limit); integer(retry_base_us,1)
        require(ttl_us<=86_400_000_000 and retry_limit<=16 and retry_base_us<=86_400_000_000)
        self.writer=writer; self.clock=clock
        self.allocation_guard=allocation_guard; self.placement_guard=placement_guard
        self.checkpoint_guard=checkpoint_guard; self.recovery_guard=recovery_guard; self.completion_guard=completion_guard
        self.ttl_us=ttl_us; self.retry_limit=retry_limit; self.retry_base_us=retry_base_us

    @staticmethod
    def _graph(conn, feature_id, revision):
        run=conn.execute('SELECT project_id,graph_revision,bible_pair_digest,active_contract_digest,phase FROM v1_feature_runs WHERE feature_id=?',(feature_id,)).fetchone()
        require(run is not None,'NOT_FOUND','NOT_FOUND')
        require(run['graph_revision']==revision,'REVISION_CONFLICT','CONFLICT')
        row=conn.execute('SELECT record_json,graph_digest,contract_digest,bible_pair_digest FROM v1_graph_revisions WHERE feature_id=? AND graph_revision=?',
                         (feature_id,revision)).fetchone()
        require(row is not None,'NOT_FOUND','NOT_FOUND')
        require(row['contract_digest']==run['active_contract_digest'],'REVISION_CONFLICT','CONFLICT')
        require(row['bible_pair_digest']==run['bible_pair_digest'],'STALE_BIBLE')
        graph=json.loads(row['record_json'])
        require(graph['graph_digest']==row['graph_digest'] and
                hash_record({k:v for k,v in graph.items() if k!='graph_digest'})==row['graph_digest'],
                'EVIDENCE_INVALID')
        require(graph['project_id']==run['project_id'],'WRONG_PROJECT')
        return graph

    @staticmethod
    def _task(conn, task_id):
        row=conn.execute('SELECT * FROM v1_tasks WHERE task_id=?',(task_id,)).fetchone()
        require(row is not None,'NOT_FOUND','NOT_FOUND')
        task=json.loads(row['payload_json'])
        graph=SchedulingService._graph(conn,row['feature_id'],row['graph_revision'])
        require(task['project_id']==graph['project_id'],'WRONG_PROJECT')
        require(task['feature_contract_digest']==graph['feature_contract_digest'], 'REVISION_CONFLICT','CONFLICT')
        require(task['bible_pair_digest']==graph['bible_pair_digest'],'STALE_BIBLE')
        validate_task(task)
        require(task['task_digest']==row['task_digest'],'EVIDENCE_INVALID')
        require(any(t==task for t in graph['tasks']),'EVIDENCE_INVALID')
        return row,task,graph

    @staticmethod
    def _ready(conn, task):
        for dep in task['dependencies']:
            row=conn.execute('SELECT feature_id,graph_revision,state FROM v1_tasks WHERE task_id=?',(dep,)).fetchone()
            require(row is not None and row['feature_id']==task['feature_id']
                    and row['graph_revision']==task['graph_revision'],'WRONG_FEATURE')
            if row['state']!='SUCCEEDED': return False
        return True

    @staticmethod
    def _dispatch_allowed(conn, feature_id):
        row=conn.execute('SELECT phase FROM v1_feature_runs WHERE feature_id=?',(feature_id,)).fetchone()
        require(row is not None,'NOT_FOUND','NOT_FOUND')
        require(row['phase'] in ('PLANNED','IN_PRODUCTION','REWORK_REQUIRED'),
                'PERMISSION_DENIED','BLOCKED_LOCAL')

    @staticmethod
    def _resource_scope(conn, task):
        row=conn.execute('SELECT payload_json FROM v1_feature_contracts WHERE feature_id=? AND contract_digest=?',
                         (task['feature_id'],task['feature_contract_digest'])).fetchone()
        require(row is not None,'NOT_FOUND','NOT_FOUND')
        contract=json.loads(row['payload_json'])
        require(contract['project_id']==task['project_id'] and contract['bible_pair_digest']==task['bible_pair_digest'],
                'WRONG_FEATURE')
        return (contract.get('repository_ref'),contract.get('feature_branch'))

    def _retry_due(self,conn,task_id,now):
        rows=conn.execute("SELECT finished_us FROM v1_task_attempts WHERE task_id=? AND status='FAILED_TRANSIENT' ORDER BY lease_epoch DESC",(task_id,)).fetchall()
        if not rows: return True
        if len(rows)>self.retry_limit: return False
        finished=rows[0]['finished_us']
        return finished is not None and now >= finished+self.retry_base_us*(2**(len(rows)-1))

    def enqueue_ready(self, feature_id: str, graph_revision: int, scoped_cabals: Mapping[str,Any]) -> Result:
        try:
            ident(feature_id); integer(graph_revision,1)
            require(self.allocation_guard is not None,'TEST_NOT_RUN','BLOCKED_LOCAL')
            # Trusted CAB-G/CAB-A allocation and graph-digest binding must be verified by P2.
            decision=self.allocation_guard(feature_id,graph_revision,scoped_cabals)
            require(isinstance(decision,Decision) and decision.allowed and decision.status=='ALLOW'
                    and decision.code=='OK' and bool(decision.evidence_refs),'EVIDENCE_INVALID')
            now=self.clock(); integer(now)
            def tx(conn):
                graph=self._graph(conn,feature_id,graph_revision)
                self._dispatch_allowed(conn,feature_id)
                rows=conn.execute('SELECT task_id,state FROM v1_tasks WHERE feature_id=? AND graph_revision=?',
                                  (feature_id,graph_revision)).fetchall()
                states={r['task_id']:r['state'] for r in rows}; completed={k for k,v in states.items() if v=='SUCCEEDED'}
                promoted=[]
                for t in graph['tasks']:
                    tid=t['task_id']; state=states.get(tid)
                    require(state is not None,'NOT_FOUND','NOT_FOUND')
                    if state in ('PENDING','RETRY_WAIT') and self._ready(conn,t):
                        if state=='RETRY_WAIT' and not self._retry_due(conn,tid,now): continue
                        conn.execute("UPDATE v1_tasks SET state='READY' WHERE task_id=? AND state=?",(tid,state))
                        promoted.append(tid)
                trace=critical_path(graph,completed)
                for item in trace: item['state']='READY' if item['task_id'] in promoted else states[item['task_id']]
                return {'feature_id':feature_id,'graph_revision':graph_revision,'promoted':promoted,
                        'critical_path':trace}
            return Result('OK','OK',self.writer.write(tx),evidence_refs=tuple(decision.evidence_refs))
        except Refused as exc: return refused(exc)

    def lease_task(self, task_id: str, worker_principal: str, approved_capacity: Decision) -> Result:
        try:
            ident(task_id); ident(worker_principal)
            require(isinstance(approved_capacity,Decision) and approved_capacity.allowed
                    and approved_capacity.status=='ALLOW' and approved_capacity.code=='OK'
                    and approved_capacity.affected_ref==task_id and bool(approved_capacity.evidence_refs),
                    'PERMISSION_DENIED')
            require(self.placement_guard is not None,'TEST_NOT_RUN','BLOCKED_LOCAL')
            def read(conn):
                row,task,graph=self._task(conn,task_id)
                require(row['state']=='READY','REVISION_CONFLICT','CONFLICT')
                require(self._ready(conn,task),'REVISION_CONFLICT','CONFLICT')
                return task,graph['graph_digest']
            task,graph_digest=self.writer.read(read)
            guard=self.placement_guard(task,worker_principal,approved_capacity)
            require(isinstance(guard,Decision) and guard.allowed and guard.status=='ALLOW' and guard.code=='OK'
                    and guard.affected_ref==task_id and bool(guard.evidence_refs), 'PERMISSION_DENIED')
            # The guard's binding to exact scope and graph digest must be guaranteed by P2 authority.
            now=self.clock(); integer(now)
            def tx(conn):
                row,current,graph=self._task(conn,task_id)
                self._dispatch_allowed(conn,task['feature_id'])
                require(graph['graph_digest']==graph_digest and current==task,'REVISION_CONFLICT','CONFLICT')
                require(row['state']=='READY' and self._ready(conn,current),'REVISION_CONFLICT','CONFLICT')
                active=conn.execute('SELECT 1 FROM v1_worker_leases WHERE task_id=? AND fenced=0 AND expires_at_us>?',
                                    (task_id,now)).fetchone()
                require(active is None,'LEASE_CONFLICT','CONFLICT')
                # Check simultaneous leases for other tasks owning overlapping canonical resources.
                resources=[r.casefold() for r in task['owned_resources']]
                source_scope=self._resource_scope(conn,task)
                for other in conn.execute("SELECT l.task_id,t.payload_json FROM v1_worker_leases l JOIN v1_tasks t ON t.task_id=l.task_id WHERE l.fenced=0 AND l.expires_at_us>?",(now,)):
                    o=json.loads(other['payload_json'])
                    other_scope=self._resource_scope(conn,o)
                    # Two distinct branches have independent workspaces.
                    if all(source_scope) and all(other_scope) and source_scope!=other_scope:
                        continue
                    for a in resources:
                        for b in o['owned_resources']:
                            b=b.casefold()
                            require(not (a==b or a.startswith(b+'/') or b.startswith(a+'/')),'LEASE_CONFLICT','CONFLICT')
                prior=conn.execute("SELECT COUNT(*) FROM v1_task_attempts WHERE task_id=?",(task_id,)).fetchone()[0]
                require(prior<=self.retry_limit,'CAPACITY_LIMIT')
                # CAB-H is sole host-session writer; never invent a synthetic
                # provider session ID from task text. Read verified session state.
                sessions=conn.execute('SELECT session_id,session_epoch FROM v1_host_sessions WHERE authenticated_principal=? AND observed_real=1 ORDER BY session_epoch DESC,session_id LIMIT 2',
                                      (worker_principal,)).fetchall()
                require(bool(sessions),'PROVIDER_NOT_VERIFIED','BLOCKED_LOCAL')
                require(len(sessions)==1 or sessions[0]['session_epoch']!=sessions[1]['session_epoch'],
                        'PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
                lease=begin_attempt(conn,task_id,worker_principal,now,self.ttl_us,
                                    hash_record(task['input_digests']),sessions[0]['session_id'])
                conn.execute("UPDATE v1_tasks SET state='LEASED' WHERE task_id=? AND state='READY'",(task_id,))
                return {'lease':lease,'task':current,'graph_digest':graph['graph_digest']}
            result=self.writer.write(tx)
            return Result('OK','OK',result,receipt_ref=result['lease']['lease_id'],
                          evidence_refs=tuple(approved_capacity.evidence_refs)+tuple(guard.evidence_refs))
        except Refused as exc: return refused(exc)

    def record_checkpoint(self, task_id: str, lease_epoch: int, host_receipt: Mapping[str,Any]) -> Result:
        try:
            ident(task_id); integer(lease_epoch,1)
            require(self.checkpoint_guard is not None,'TEST_NOT_RUN','BLOCKED_LOCAL')
            require(isinstance(host_receipt,Mapping) and set(host_receipt)=={'task_id','lease_epoch','host_receipt_ref','content_digest','session_id'})
            require(host_receipt['task_id']==task_id and host_receipt['lease_epoch']==lease_epoch,'LEASE_FENCED','CONFLICT')
            ident(host_receipt['host_receipt_ref']); digest(host_receipt['content_digest']); ident(host_receipt['session_id'])
            verdict=self.checkpoint_guard(host_receipt)
            require(isinstance(verdict,Decision) and verdict.allowed and verdict.status=='ALLOW'
                    and verdict.code=='OK' and bool(verdict.evidence_refs),'EVIDENCE_INVALID')
            now=self.clock(); integer(now)
            def tx(conn):
                row,t,g=self._task(conn,task_id)
                require(row['state']=='LEASED','LEASE_FENCED','CONFLICT')
                lease=active_lease(conn,task_id,lease_epoch,now)
                require(lease['session_id']==host_receipt['session_id'],'EVIDENCE_INVALID')
                ref=host_receipt['host_receipt_ref']
                exists=conn.execute('SELECT content_digest FROM v1_task_checkpoints WHERE host_receipt_ref=? AND task_id=? AND lease_epoch=?',
                                    (ref,task_id,lease_epoch)).fetchone()
                if exists:
                    require(exists['content_digest']==host_receipt['content_digest'],'IDEMPOTENCY_CONFLICT','CONFLICT')
                    return ref
                from uuid import uuid4
                conn.execute('INSERT INTO v1_task_checkpoints(checkpoint_id,task_id,lease_epoch,host_receipt_ref,content_digest,created_at_us) VALUES(?,?,?,?,?,?)',
                             ('checkpoint:'+uuid4().hex,task_id,lease_epoch,ref,host_receipt['content_digest'],now))
                return ref
            ref=self.writer.write(tx)
            return Result('OK','OK',{'task_id':task_id,'lease_epoch':lease_epoch,'host_receipt_ref':ref},
                          receipt_ref=ref,evidence_refs=tuple(verdict.evidence_refs))
        except Refused as exc: return refused(exc)

    def finish_attempt(self, task_id: str, lease_epoch: int, verified_artifacts: Mapping[str,Any]) -> Result:
        try:
            ident(task_id); integer(lease_epoch,1)
            require(self.completion_guard is not None,'TEST_NOT_RUN','BLOCKED_LOCAL')
            require(isinstance(verified_artifacts,Mapping) and set(verified_artifacts)=={
                'task_id','lease_epoch','status','result_digest','manifest_ref','evidence_refs'})
            require(verified_artifacts['task_id']==task_id and verified_artifacts['lease_epoch']==lease_epoch,'LEASE_FENCED','CONFLICT')
            require(verified_artifacts['status'] in ('SUCCEEDED','INFRA_TRANSIENT','SEMANTIC_FEATURE'))
            digest(verified_artifacts['result_digest'])
            require(type(verified_artifacts['evidence_refs']) is list and len(verified_artifacts['evidence_refs'])>0)
            for r in verified_artifacts['evidence_refs']: ident(r)
            if verified_artifacts['status']=='SUCCEEDED': ident(verified_artifacts['manifest_ref'])
            else: require(verified_artifacts['manifest_ref'] is None)
            def read(conn):
                row,task,g=self._task(conn,task_id)
                require(row['state']=='LEASED','LEASE_FENCED','CONFLICT')
                active_lease(conn,task_id,lease_epoch,self.clock())
                return task
            task=self.writer.read(read)
            guard=self.completion_guard(task,verified_artifacts)
            require(isinstance(guard,Decision) and guard.allowed and guard.status=='ALLOW'
                    and guard.code=='OK' and bool(guard.evidence_refs),'EVIDENCE_INVALID')
            now=self.clock(); integer(now)
            status=verified_artifacts['status']
            def tx(conn):
                row,current,graph=self._task(conn,task_id)
                require(current==task and row['state']=='LEASED','REVISION_CONFLICT','CONFLICT')
                active_lease(conn,task_id,lease_epoch,now)
                count=conn.execute('SELECT COUNT(*) FROM v1_task_attempts WHERE task_id=?',(task_id,)).fetchone()[0]
                if status=='SUCCEEDED':
                    close_attempt(conn,task_id,lease_epoch,'SUCCEEDED',now,verified_artifacts['result_digest'])
                    conn.execute("UPDATE v1_tasks SET state='SUCCEEDED',output_manifest_ref=? WHERE task_id=?",
                                 (verified_artifacts['manifest_ref'],task_id))
                    state='SUCCEEDED'
                else:
                    transient=status=='INFRA_TRANSIENT'
                    close_attempt(conn,task_id,lease_epoch,'FAILED_TRANSIENT' if transient else 'FAILED_SEMANTIC',
                                  now,verified_artifacts['result_digest'])
                    state='RETRY_WAIT' if transient and count<=self.retry_limit else 'FAILED'
                    conn.execute('UPDATE v1_tasks SET state=? WHERE task_id=?',(state,task_id))
                return {'task_id':task_id,'lease_epoch':lease_epoch,'task_state':state,
                        'failure_class':None if status=='SUCCEEDED' else status,
                        'graph_revision':graph['revision']}
            record=self.writer.write(tx)
            return Result('OK','OK',record,receipt_ref=verified_artifacts['result_digest'],
                          evidence_refs=tuple(guard.evidence_refs))
        except Refused as exc: return refused(exc)

    def fence_expired(self, project_id: str, trusted_now_us: int) -> Result:
        try:
            ident(project_id); integer(trusted_now_us)
            now=self.clock(); integer(now)
            require(now==trusted_now_us,'EVIDENCE_INVALID')
            def tx(conn):
                rows=conn.execute('''SELECT l.lease_id,l.task_id,l.epoch,l.expires_at_us,t.state,t.feature_id
                                     FROM v1_worker_leases l JOIN v1_tasks t ON t.task_id=l.task_id
                                     JOIN v1_feature_runs f ON f.feature_id=t.feature_id
                                     WHERE f.project_id=? AND l.fenced=0 AND l.expires_at_us<=?''',
                                  (project_id,now)).fetchall()
                fenced=[]
                for lease in rows:
                    conn.execute('UPDATE v1_worker_leases SET fenced=1 WHERE lease_id=?',(lease['lease_id'],))
                    conn.execute("UPDATE v1_task_attempts SET status='FENCED',finished_us=? WHERE task_id=? AND lease_epoch=? AND status='ACTIVE'",
                                 (now,lease['task_id'],lease['epoch']))
                    # On lease timeout, recover only after trusted provider readback: no duplicate effect.
                    if lease['state']=='LEASED':
                        conn.execute("UPDATE v1_tasks SET state='RECOVERY_BLOCKED' WHERE task_id=?",(lease['task_id'],))
                    fenced.append({'task_id':lease['task_id'],'epoch':lease['epoch'],
                                   'recovery_required':True})
                return {'fenced':fenced,'requires_provider_reconciliation':bool(fenced)}
            record=self.writer.write(tx)
            return Result('OK','OK',record)
        except Refused as exc: return refused(exc)

    def reconcile_expired(self, task_id: str, lease_epoch: int, provider_observation: Mapping[str,Any]) -> Result:
        """P2 recovery-loop hook, outside the five frozen caller-facing port methods.

        Every uncertain effect MUST receive an authoritative provider readback.
        An observed completed effect retains its verified output; an observed
        absent effect becomes retryable if capacity remains. UNKNOWN remains
        blocked; it never causes a speculative redispatch.
        """
        try:
            ident(task_id); integer(lease_epoch,1)
            require(self.recovery_guard is not None,'TEST_NOT_RUN','BLOCKED_LOCAL')
            require(isinstance(provider_observation,Mapping) and
                    set(provider_observation)=={'task_id','lease_epoch','provider_state',
                                               'result_digest','manifest_ref','evidence_refs'})
            require(provider_observation['task_id']==task_id and provider_observation['lease_epoch']==lease_epoch,
                    'LEASE_FENCED','CONFLICT')
            require(provider_observation['provider_state'] in ('APPLIED','ABSENT','UNKNOWN'))
            require(type(provider_observation['evidence_refs']) is list and len(provider_observation['evidence_refs'])>0)
            for ref in provider_observation['evidence_refs']: ident(ref)
            if provider_observation['provider_state']=='APPLIED':
                digest(provider_observation['result_digest']); ident(provider_observation['manifest_ref'])
            else:
                require(provider_observation['manifest_ref'] is None and provider_observation['result_digest'] is None)
            if provider_observation['provider_state']=='UNKNOWN':
                return Result('UNKNOWN_EXTERNAL','PROVIDER_UNKNOWN',evidence_refs=tuple(provider_observation['evidence_refs']))
            # Authenticated provider-native observation; do not trust caller assertions.
            guard=self.recovery_guard(provider_observation)
            require(isinstance(guard,Decision) and guard.allowed and guard.status=='ALLOW'
                    and guard.code=='OK' and bool(guard.evidence_refs),'EVIDENCE_INVALID')
            artifact_refs=()
            if provider_observation['provider_state']=='APPLIED':
                require(self.completion_guard is not None,'TEST_NOT_RUN','BLOCKED_LOCAL')
                task=self.writer.read(lambda conn:self._task(conn,task_id)[1])
                complete={'task_id':task_id,'lease_epoch':lease_epoch,'status':'SUCCEEDED',
                          'result_digest':provider_observation['result_digest'],
                          'manifest_ref':provider_observation['manifest_ref'],
                          'evidence_refs':provider_observation['evidence_refs']}
                attestation=self.completion_guard(task,complete)
                require(isinstance(attestation,Decision) and attestation.allowed and
                        attestation.status=='ALLOW' and attestation.code=='OK' and
                        bool(attestation.evidence_refs),'EVIDENCE_INVALID')
                artifact_refs=tuple(attestation.evidence_refs)
            def tx(conn):
                row,task,graph=self._task(conn,task_id)
                require(row['state']=='RECOVERY_BLOCKED','LOCAL_RECOVERY_REQUIRED','CONFLICT')
                lease=conn.execute('SELECT epoch,fenced FROM v1_worker_leases WHERE task_id=? ORDER BY epoch DESC LIMIT 1',
                                   (task_id,)).fetchone()
                require(lease is not None and lease['epoch']==lease_epoch and lease['fenced']==1,
                        'LEASE_FENCED','CONFLICT')
                if provider_observation['provider_state']=='APPLIED':
                    conn.execute("UPDATE v1_task_attempts SET status='SUCCEEDED',result_digest=?,finished_us=COALESCE(finished_us,?) WHERE task_id=? AND lease_epoch=? AND status='FENCED'",
                                 (provider_observation['result_digest'],self.clock(),task_id,lease_epoch))
                    conn.execute("UPDATE v1_tasks SET state='SUCCEEDED',output_manifest_ref=? WHERE task_id=?",
                                 (provider_observation['manifest_ref'],task_id))
                    next_state='SUCCEEDED'
                else:
                    count=conn.execute('SELECT COUNT(*) FROM v1_task_attempts WHERE task_id=?',(task_id,)).fetchone()[0]
                    next_state='READY' if count<=self.retry_limit and self._ready(conn,task) else 'FAILED'
                    conn.execute('UPDATE v1_tasks SET state=? WHERE task_id=?',(next_state,task_id))
                return {'task_id':task_id,'lease_epoch':lease_epoch,'task_state':next_state,
                        'provider_state':provider_observation['provider_state']}
            return Result('OK','OK',self.writer.write(tx),
                          evidence_refs=tuple(guard.evidence_refs)+artifact_refs)
        except Refused as exc: return refused(exc)
