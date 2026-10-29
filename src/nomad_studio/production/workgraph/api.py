"""CAB-C WorkGraphPort; only CAB-C tables are mutated.

P2 injects a table-capability-limited SQLite writer with read(fn) and write(fn).
The writer must serialize write(fn) under BEGIN IMMEDIATE with FK enforcement.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Sequence

from .core import (Decision, Refused, Result, canonical, critical_path, digest,
    ident, make_graph, refused, require, validate_contract, validate_graph)


class WorkGraphService:
    """Immutable graph revisions. FeatureRun graph CAS activation belongs to CAB-J/P2."""

    def __init__(self, writer: Any, *, rework_guard: Callable[[Mapping[str,Any]], Decision] | None = None,
                 plan_guard: Callable[[Mapping[str,Any],Mapping[str,Any]], Decision] | None = None):
        self.writer=writer
        self.rework_guard=rework_guard
        self.plan_guard=plan_guard

    @staticmethod
    def _authority(conn: Any, feature_id: str, contract_digest: str | None = None) -> tuple[dict,dict]:
        run=conn.execute('SELECT * FROM v1_feature_runs WHERE feature_id=?',(feature_id,)).fetchone()
        require(run is not None,'NOT_FOUND','NOT_FOUND')
        run=dict(run)
        query='SELECT * FROM v1_feature_contracts WHERE feature_id=? AND contract_digest=?'
        row=conn.execute(query,(feature_id,contract_digest or run['active_contract_digest'])).fetchone()
        require(row is not None,'NOT_FOUND','NOT_FOUND')
        row=dict(row)
        require(run['project_id']==row['project_id'],'WRONG_PROJECT')
        require(row['bible_pair_digest']==run['bible_pair_digest'],'STALE_BIBLE')
        require(row['contract_digest']==run['active_contract_digest'],'REVISION_CONFLICT','CONFLICT')
        return run,row

    def plan_feature(self, feature_contract: Mapping[str,Any], capability_snapshot: Mapping[str,Any]) -> Result:
        try:
            c=validate_contract(feature_contract)
            require(self.plan_guard is not None,'TEST_NOT_RUN','BLOCKED_LOCAL')
            verdict=self.plan_guard(c,capability_snapshot)
            require(isinstance(verdict,Decision) and verdict.allowed and verdict.status=='ALLOW'
                    and verdict.code=='OK' and bool(verdict.evidence_refs), 'EVIDENCE_INVALID')
            def tx(conn: Any) -> dict:
                run,stored=self._authority(conn,c['feature_id'],c['contract_digest'])
                require(run['project_id']==c['project_id'],'WRONG_PROJECT')
                require(stored['bible_pair_digest']==c['bible_pair_digest'],'STALE_BIBLE')
                require(stored['contract_digest']==c['contract_digest'],'REVISION_CONFLICT','CONFLICT')
                require(stored['payload_json'] and json.loads(stored['payload_json'])==c,
                        'EVIDENCE_INVALID')
                previous=conn.execute('SELECT COALESCE(MAX(graph_revision),0) FROM v1_graph_revisions WHERE feature_id=?',
                                      (c['feature_id'],)).fetchone()[0]
                # Never race a new unpublished graph revision from a competing planner.
                require(previous==run['graph_revision'],'REVISION_CONFLICT','CONFLICT')
                g=make_graph(c,capability_snapshot,previous+1)
                self._insert_graph(conn,g)
                return g
            graph=self.writer.write(tx)
            return Result('OK','OK',graph,receipt_ref=graph['graph_digest'],evidence_refs=tuple(verdict.evidence_refs))
        except Refused as exc:
            return refused(exc)

    @staticmethod
    def _insert_graph(conn: Any, g: Mapping[str,Any], carried: Mapping[str, tuple[str,str | None]] | None = None) -> None:
        conn.execute('INSERT INTO v1_graph_revisions(feature_id,graph_revision,graph_digest,contract_digest,bible_pair_digest,record_json) VALUES(?,?,?,?,?,?)',
            (g['feature_id'],g['revision'],g['graph_digest'],g['feature_contract_digest'],g['bible_pair_digest'],canonical(g)))
        for t in g['tasks']:
            state,manifest=(carried or {}).get(t['task_id'],('PENDING',None))
            conn.execute('INSERT INTO v1_tasks(feature_id,task_id,graph_revision,cabal_id,task_digest,state,output_manifest_ref,budget_limit_units,payload_json) VALUES(?,?,?,?,?,?,?,?,?)',
                (g['feature_id'],t['task_id'],g['revision'],t['cabal_id'],t['task_digest'],state,manifest,t['budget_limit_units'],canonical(t)))
        for t in g['tasks']:
            for parent in t['dependencies']:
                conn.execute('INSERT INTO v1_dependencies(feature_id,parent_id,child_id) VALUES(?,?,?)',
                    (g['feature_id'],parent,t['task_id']))

    def validate_graph(self, graph: Mapping[str,Any], contract_digest: str) -> Decision:
        affected_ref=str(graph.get('graph_id','')) if isinstance(graph,Mapping) else ''
        try:
            digest(contract_digest)
            g=validate_graph(graph,contract_digest)
            def read(conn: Any) -> dict:
                run,stored=self._authority(conn,g['feature_id'],contract_digest)
                require(run['project_id']==g['project_id'],'WRONG_PROJECT')
                require(g['bible_pair_digest']==run['bible_pair_digest'],'STALE_BIBLE')
                c=validate_contract(json.loads(stored['payload_json']))
                validate_graph(g,contract_digest,
                    deliverables={d['deliverable_id'] for d in c['deliverables']},
                    acceptance=set(c['acceptance_ids']),budget=c['budget_limit_units'])
                stored_graph=conn.execute('SELECT graph_digest FROM v1_graph_revisions WHERE feature_id=? AND graph_revision=?',
                                          (g['feature_id'],g['revision'])).fetchone()
                require(stored_graph is not None,'NOT_FOUND','NOT_FOUND')
                require(stored_graph['graph_digest']==g['graph_digest'],'EVIDENCE_INVALID')
                return c
            self.writer.read(read)
            return Decision(True,'ALLOW','OK',g['graph_id'],(g['graph_digest'],))
        except Refused as exc:
            return Decision(False,'DENY',exc.code,affected_ref)

    def create_rework(self, feature_id: str, failed_receipt: Mapping[str,Any], current_graph_revision: int) -> Result:
        try:
            ident(feature_id)
            require(type(current_graph_revision) is int and current_graph_revision>0)
            require(self.rework_guard is not None,'TEST_NOT_RUN','BLOCKED_LOCAL')
            require(isinstance(failed_receipt,Mapping))
            keys={'trigger_task_ids','new_input_digests','evidence_refs','reason'}
            require(failed_receipt.keys()==keys)
            require(failed_receipt['reason'] in ('FAILED_TEST','OWNER_CHANGE','CHANGED_INPUT'))
            triggers=failed_receipt['trigger_task_ids']
            require(type(triggers) is list and len(triggers)>0 and len(triggers)==len(set(triggers)))
            for t in triggers: ident(t)
            require(type(failed_receipt['evidence_refs']) is list and len(failed_receipt['evidence_refs'])>0)
            for r in failed_receipt['evidence_refs']: ident(r)
            inputs=failed_receipt['new_input_digests']
            require(isinstance(inputs,Mapping) and set(inputs)<=set(triggers))
            for v in inputs.values():
                require(type(v) is list)
                for x in v: digest(x)
            verdict=self.rework_guard(failed_receipt)
            require(isinstance(verdict,Decision) and verdict.allowed and verdict.status=='ALLOW' and verdict.code=='OK'
                    and bool(verdict.evidence_refs),'EVIDENCE_INVALID')
            def tx(conn: Any) -> dict:
                run,stored=self._authority(conn,feature_id)
                require(run['graph_revision']==current_graph_revision,'REVISION_CONFLICT','CONFLICT')
                latest=conn.execute('SELECT MAX(graph_revision) FROM v1_graph_revisions WHERE feature_id=?',
                                    (feature_id,)).fetchone()[0]
                require(latest==current_graph_revision,'REVISION_CONFLICT','CONFLICT')
                row=conn.execute('SELECT record_json FROM v1_graph_revisions WHERE feature_id=? AND graph_revision=?',
                                 (feature_id,current_graph_revision)).fetchone()
                require(row is not None,'NOT_FOUND','NOT_FOUND')
                previous=json.loads(row[0]); tasks={t['task_id']:t for t in previous['tasks']}
                require(set(triggers)<=tasks.keys(),'WRONG_FEATURE')
                children={k:set() for k in tasks}
                for t in tasks.values():
                    for dep in t['dependencies']: children[dep].add(t['task_id'])
                affected=set(triggers); stack=list(triggers)
                while stack:
                    for child in children[stack.pop()]:
                        if child not in affected: affected.add(child); stack.append(child)
                # Rework atomically fences every active old attempt; no untrusted completion after revision changes.
                for oldid in tasks:
                    conn.execute('UPDATE v1_worker_leases SET fenced=1 WHERE task_id=? AND fenced=0',(oldid,))
                    conn.execute("UPDATE v1_task_attempts SET status='FENCED' WHERE task_id=? AND status='ACTIVE'",(oldid,))
                revision=current_graph_revision+1
                prefix=f'{feature_id}:r{current_graph_revision}:'
                require(all(x.startswith(prefix) for x in tasks))
                new_prefix=f'{feature_id}:r{revision}:'
                lookup={tid:new_prefix+tid[len(prefix):] for tid in tasks}
                from .core import hash_record
                new_tasks=[]; carried={}
                for oldid,t in tasks.items():
                    item={k:v for k,v in t.items() if k!='task_digest'}
                    item['task_id']=lookup[oldid]
                    item['graph_revision']=revision
                    item['dependencies']=[lookup[d] for d in t['dependencies']]
                    if oldid in inputs: item['input_digests']=sorted(set(inputs[oldid]))
                    item['task_digest']=hash_record(item)
                    new_tasks.append(item)
                    if oldid not in affected:
                        old=conn.execute('SELECT state,output_manifest_ref FROM v1_tasks WHERE task_id=?',(oldid,)).fetchone()
                        if old and old['state']=='SUCCEEDED' and old['output_manifest_ref']:
                            carried[item['task_id']]=('SUCCEEDED',old['output_manifest_ref'])
                new_graph={k:v for k,v in previous.items() if k not in ('tasks','graph_digest')}
                new_graph.update(revision=revision,graph_id=f'{feature_id}:r{revision}',tasks=new_tasks)
                new_graph['graph_digest']=hash_record(new_graph)
                c=validate_contract(json.loads(stored['payload_json']))
                validate_graph(new_graph,c['contract_digest'],deliverables={d['deliverable_id'] for d in c['deliverables']},
                               acceptance=set(c['acceptance_ids']),budget=c['budget_limit_units'])
                # Previous graph may contain failed/queued unaffected tasks; don't carry those as complete.
                self._insert_graph(conn,new_graph,carried)
                return {'graph':new_graph,'affected':sorted(lookup[x] for x in affected),
                        'retained_output_refs':sorted(v[1] for v in carried.values())}
            result=self.writer.write(tx)
            return Result('OK','OK',result,receipt_ref=result['graph']['graph_digest'],
                          evidence_refs=tuple(verdict.evidence_refs))
        except Refused as exc: return refused(exc)

    def get_ready_tasks(self, feature_id: str, graph_revision: int) -> Sequence[Mapping[str,Any]]:
        """Read-only; publication/dispatch can only use the current CAB-J-activated revision."""
        try:
            ident(feature_id)
            require(type(graph_revision) is int and graph_revision>0)
            def read(conn: Any) -> list[Mapping[str,Any]]:
                run,_=self._authority(conn,feature_id)
                require(run['graph_revision']==graph_revision,'REVISION_CONFLICT','CONFLICT')
                rows=conn.execute('SELECT task_id,state,payload_json FROM v1_tasks WHERE feature_id=? AND graph_revision=?',
                                  (feature_id,graph_revision)).fetchall()
                require(bool(rows),'NOT_FOUND','NOT_FOUND')
                completed={r['task_id'] for r in rows if r['state']=='SUCCEEDED'}
                tasks=[]
                for r in rows:
                    if r['state'] in ('PENDING','READY'):
                        t=json.loads(r['payload_json'])
                        if set(t['dependencies'])<=completed: tasks.append(t)
                graphrow=conn.execute('SELECT record_json,graph_digest FROM v1_graph_revisions WHERE feature_id=? AND graph_revision=?',
                                      (feature_id,graph_revision)).fetchone()
                graph=json.loads(graphrow['record_json'])
                from .core import hash_record
                require(graphrow['graph_digest']==hash_record({k:v for k,v in graph.items() if k!='graph_digest'}),
                        'EVIDENCE_INVALID')
                trace=critical_path(graph,completed)
                weight={t['task_id']:t['remaining_critical_path'] for t in trace}
                return sorted(tasks,key=lambda t:(-weight[t['task_id']],t['task_id']))
            return self.writer.read(read)
        except Refused:
            return ()  # Frozen Sequence return has no Result status; fail closed.

    def readiness_trace(self, feature_id: str, graph_revision: int) -> Result:
        try:
            ident(feature_id)
            def read(conn):
                run,_=self._authority(conn,feature_id)
                require(run['graph_revision']==graph_revision,'REVISION_CONFLICT','CONFLICT')
                row=conn.execute('SELECT record_json FROM v1_graph_revisions WHERE feature_id=? AND graph_revision=?',
                                 (feature_id,graph_revision)).fetchone()
                require(row is not None,'NOT_FOUND','NOT_FOUND')
                states={r['task_id']:r['state'] for r in conn.execute('SELECT task_id,state FROM v1_tasks WHERE feature_id=? AND graph_revision=?',(feature_id,graph_revision))}
                trace=critical_path(json.loads(row[0]),{k for k,v in states.items() if v=='SUCCEEDED'})
                for t in trace: t['state']=states[t['task_id']]
                return {'feature_id':feature_id,'graph_revision':graph_revision,'critical_path':trace}
            return Result('OK','OK',self.writer.read(read))
        except Refused as exc: return refused(exc)
