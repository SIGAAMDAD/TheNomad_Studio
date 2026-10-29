"""R9 owner-readable status from authoritative CAB-J and read-only task/QA/cost tables.

This service never publishes a QA_READY conclusion from self-authored text.
All measured values derive from recorded provider/runner rows. Missing timings
are reported as unknown, not zero-cost proof.
"""
from __future__ import annotations
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any
from nomad_studio.production.director.wire import Result, ok, fail, valid_id


class ProductionStatusService:
    def __init__(self, writer: Any):
        self.writer = writer  # read across service tables, owner-guarded

    def get_feature(self, owner_id: str, feature_id: str) -> Result:
        if not valid_id(owner_id) or not valid_id(feature_id):
            return fail('REJECTED', 'INVALID_INPUT')
        with self.writer.transaction() as tx:
            row = tx.execute('SELECT r.project_id,r.phase,r.revision,r.graph_revision,r.active_contract_digest,r.bible_pair_digest,r.qa_branch_ref FROM v1_feature_runs r JOIN v1_projects p ON p.project_id=r.project_id WHERE r.feature_id=? AND p.owner_id=?', (feature_id, owner_id)).fetchone()
            if row is None:
                return fail('NOT_FOUND', 'NOT_FOUND')
            project, state, revision, graph_revision, contract, bible, sha = row
            task_rows = tx.execute('SELECT task_id,state,budget_limit_units FROM v1_tasks WHERE feature_id=? AND graph_revision=? ORDER BY task_id', (feature_id, graph_revision)).fetchall()
            ids = [r[0] for r in task_rows]
            deps = tx.execute('SELECT parent_id,child_id FROM v1_dependencies WHERE feature_id=?', (feature_id,)).fetchall()
            attempts = tx.execute('SELECT a.task_id,a.started_us,a.finished_us,a.status FROM v1_task_attempts a JOIN v1_tasks t ON t.task_id=a.task_id WHERE t.feature_id=? AND t.graph_revision=?', (feature_id, graph_revision)).fetchall()
            costs = tx.execute('SELECT COALESCE(SUM(s.measured_units),0),COUNT(s.settlement_id) FROM v1_budget_settlements s JOIN v1_budget_reservations r ON r.reservation_id=s.reservation_id WHERE r.feature_id=?', (feature_id,)).fetchone()
            reservations = tx.execute('SELECT COALESCE(SUM(reserved_units),0),COALESCE(SUM(measured_units),0),COUNT(*) FROM v1_budget_reservations WHERE feature_id=?', (feature_id,)).fetchone()
            qa = tx.execute('SELECT receipt_id,commit_sha,quality_signer FROM v1_qa_ready_receipts WHERE feature_id=? AND commit_sha=?', (feature_id, sha)).fetchone() if sha else None
            rereview = tx.execute("SELECT COUNT(*) FROM v1_review_verdicts v JOIN v1_review_assignments a ON a.review_id=v.review_id WHERE a.feature_id=? AND v.verdict='REJECT'", (feature_id,)).fetchone()[0]
        duration = defaultdict(int)
        completed = set()
        unknown = set(ids)
        for task, start, end, attempt_state in attempts:
            if start is not None and end is not None and end >= start:
                duration[task] += end-start
                unknown.discard(task)
            if attempt_state in ('COMPLETED', 'VERIFIED', 'ACCEPTED'):
                completed.add(task)
        critical = self._critical_path(ids, deps, duration, unknown)
        record = {
            'project_id': project, 'feature_id': feature_id, 'status': state,
            'revision': revision, 'graph_revision': graph_revision,
            'feature_contract_digest': contract, 'bible_pair_digest': bible,
            'source_commit': sha, 'qa_ready_receipt_ref': qa[0] if qa else None,
            'qa_signer': qa[2] if qa else None,
            'tasks': [{'task_id': t, 'status': s} for t,s,_ in task_rows],
            'completed_attempts': len(completed), 'rework_rejections': rereview,
            'settled_units': costs[0], 'settlement_count': costs[1],
            'reserved_units': reservations[0], 'reported_measured_units': reservations[1],
            'budget_reservation_count': reservations[2],
            'cost_complete': reservations[2] == costs[1] and reservations[1] == costs[0] and bool(reservations[2]),
            **critical,
        }
        return ok(record)

    def get_studio(self, owner_id: str, project_id: str | None) -> Result:
        if not valid_id(owner_id) or (project_id is not None and not valid_id(project_id)):
            return fail('REJECTED', 'INVALID_INPUT')
        with self.writer.transaction() as tx:
            projects = tx.execute('SELECT project_id FROM v1_projects WHERE owner_id=?' + (' AND project_id=?' if project_id else ''),
                                  (owner_id, project_id) if project_id else (owner_id,)).fetchall()
            if project_id and not projects:
                return fail('NOT_FOUND', 'NOT_FOUND')
            selected = [p[0] for p in projects]
            rows = []
            for project in selected:
                rows.extend(tx.execute('SELECT feature_id,phase,revision FROM v1_feature_runs WHERE project_id=? ORDER BY feature_id', (project,)).fetchall())
        return ok({'owner_principal_id': owner_id, 'projects': selected,
                   'features': [{'feature_id': f, 'status': status, 'revision': revision} for f,status,revision in rows]})

    @staticmethod
    def _critical_path(ids: list[str], deps: list[tuple[str,str]], durations: Mapping[str,int], unknown: set[str]) -> dict[str,Any]:
        reachable = set(ids)
        parents = defaultdict(set)
        children = defaultdict(set)
        for parent, child in deps:
            if parent in reachable and child in reachable:
                parents[child].add(parent)
                children[parent].add(child)
        degree = {i: len(parents[i]) for i in ids}
        q = deque(sorted(i for i in ids if degree[i] == 0))
        best = {}
        paths = {}
        visited = 0
        while q:
            current = q.popleft()
            visited += 1
            predecessors = parents[current]
            winner = max(predecessors, key=lambda x: (best[x],x)) if predecessors else None
            best[current] = (best[winner] if winner else 0) + durations.get(current,0)
            paths[current] = paths[winner] + [current] if winner else [current]
            for downstream in sorted(children[current]):
                degree[downstream] -= 1
                if degree[downstream] == 0:
                    q.append(downstream)
        if visited != len(ids):
            return {'critical_path_state': 'INVALID_GRAPH', 'critical_path_task_ids': [], 'critical_path_elapsed_us': None}
        if not ids:
            return {'critical_path_state': 'NOT_MEASURED', 'critical_path_task_ids': [], 'critical_path_elapsed_us': None}
        last = max(ids, key=lambda x: (best[x],x))
        path = paths[last]
        return {'critical_path_state': 'PARTIAL' if unknown.intersection(path) else 'MEASURED',
                'critical_path_task_ids': path,
                'critical_path_elapsed_us': None if unknown.intersection(path) else best[last],
                'task_timing_gaps': sorted(unknown)}
