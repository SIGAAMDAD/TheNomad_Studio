"""Bounded department/head projections with truthful missing-observation states."""
from studio_v16.db.store import Denied
from studio_v16.db.sqlite.publication import row
from studio_v16.db.sqlite.types import integer, canonical
from studio_v16.communications.service import sha

READY = '''SELECT j.job_id,j.source_repo,j.source_base_sha,j.assignment_epoch,j.version
    FROM jobs j JOIN job_departments d ON d.job_id=j.job_id
    WHERE d.department=? AND j.state IN ('APPROVED','READY','YIELDED')
    AND NOT EXISTS (SELECT 1 FROM job_dependencies dep JOIN jobs p ON p.job_id=dep.prerequisite_job_id
                    WHERE dep.job_id=j.job_id AND p.state<>'DONE')
    ORDER BY j.created_at,j.job_id LIMIT ?'''


class ControllerViews:
    def __init__(self, coordination):
        self.coordination, self.owner = coordination, coordination.owner

    def bind_job(self, job_id, department, decision_id, context):
        with self.owner.write() as (conn, now):
            scope = self.coordination._auth(conn, context, department, 'BIND_JOB')
            if not scope.owner:
                raise Denied('OWNER_APPROVAL_REQUIRED')
            decision = self.coordination._decision(conn, context, decision_id, 'JOB_DEPARTMENT',
                                                   dict(job_id=job_id, department=department), now)
            if decision['job_id'] != job_id:
                raise Denied('INVALID_SCOPE')
            prior = conn.execute('SELECT department,decision_id FROM job_departments WHERE job_id=?', (job_id,)).fetchone()
            if prior:
                if prior != (department, decision_id):
                    raise Denied('IDEMPOTENCY_CONFLICT')
                return department
            conn.execute('INSERT INTO job_departments VALUES(?,?,?,?)', (job_id, department, decision_id, now))
            return department

    def ready(self, department, context, *, limit=50):
        integer(limit, minimum=1, maximum=200)
        with self.owner.connection(readonly=True) as conn:
            self.coordination._auth(conn, context, department, 'CONTROLLER')
            return [dict(zip(('job_id', 'source_repo', 'source_base_sha', 'assignment_epoch', 'version'), r))
                    for r in conn.execute(READY, (department, limit))]

    def snapshot(self, department, context, *, limit=100):
        integer(limit, minimum=1, maximum=200)
        with self.owner.connection(readonly=True) as conn:
            conn.execute('BEGIN')
            try:
                self.coordination._auth(conn, context, department, 'CONTROLLER')
                leader = row(conn, 'SELECT principal_id,leader_epoch,delegation_paused FROM leader_state WHERE department=?', (department,))
                responses = conn.execute('''SELECT response_id,worker_id,status,started_at,ended_at,report_json IS NOT NULL
                    FROM response_attempts WHERE department=? ORDER BY started_at DESC,response_id LIMIT ?''',
                    (department, limit)).fetchall()
                jobs = conn.execute('''SELECT j.state,count(*) FROM jobs j JOIN job_departments d ON d.job_id=j.job_id
                    WHERE d.department=? GROUP BY j.state''', (department,)).fetchall()
                missing = conn.execute('''SELECT s.response_id,s.job_id,s.attempt_id FROM response_job_starts s
                    JOIN response_attempts r ON r.response_id=s.response_id LEFT JOIN response_attempt_jobs j
                    ON j.response_id=s.response_id AND j.attempt_id=s.attempt_id
                    WHERE r.department=? AND j.attempt_id IS NULL ORDER BY s.sequence LIMIT ?''', (department, limit)).fetchall()
                high_water = conn.execute('SELECT COALESCE(max(sequence),0) FROM department_events WHERE recipient_department=?', (department,)).fetchone()[0]
                return dict(department=department, leader=leader, job_states=dict(jobs), event_high_water=high_water,
                            response_limit=limit, responses=[dict(response_id=r[0], worker_id=r[1],
                            observation=r[2] if r[5] else 'UNOBSERVED', runtime_status=r[2], started_at=r[3], ended_at=r[4]) for r in responses],
                            missing_job_reports=[dict(response_id=r[0], job_id=r[1], attempt_id=r[2], status='UNKNOWN') for r in missing])
            finally:
                conn.rollback()

    def explain(self, department, context):
        with self.owner.connection(readonly=True) as conn:
            self.coordination._auth(conn, context, department, 'CONTROLLER')
            return dict(ready=conn.execute('EXPLAIN QUERY PLAN ' + READY, (department, 50)).fetchall(),
                        inbox=conn.execute('''EXPLAIN QUERY PLAN SELECT sequence FROM department_events
                            WHERE recipient_department=? AND recipient_id=? AND sequence>? ORDER BY sequence LIMIT ?''',
                            (department, context.principal_id, 0, 50)).fetchall())

    def anchor_batch(self, department, after_sequence, context, *, limit=500):
        integer(after_sequence, minimum=0)
        integer(limit, minimum=1, maximum=1000)
        with self.owner.connection(readonly=True) as conn:
            self.coordination._auth(conn, context, department, 'AUDIT_ANCHOR')
            events = conn.execute('''SELECT sequence,event_id,recipient_id,metadata_json FROM department_events
                WHERE recipient_department=? AND sequence>? ORDER BY sequence LIMIT ?''',
                (department, after_sequence, limit)).fetchall()
            return dict(department=department, sequence_from=events[0][0] if events else None,
                        sequence_to=events[-1][0] if events else None, count=len(events), batch_hash=sha(events))

    def quarantine_subtree(self, root_job_id, decision_id, context):
        with self.owner.write() as (conn, now):
            jobs = conn.execute('''WITH RECURSIVE affected(job_id) AS (
                SELECT job_id FROM jobs WHERE job_id=? UNION SELECT d.job_id FROM job_dependencies d
                JOIN affected a ON a.job_id=d.prerequisite_job_id LIMIT 1001)
                SELECT a.job_id,dep.department FROM affected a LEFT JOIN job_departments dep ON dep.job_id=a.job_id
                ORDER BY a.job_id''', (root_job_id,)).fetchall()
            if not jobs or len(jobs) > 1000:
                raise Denied('RETRYABLE_BLOCKED')
            for _, department in jobs:
                scope = self.coordination._auth(conn, context, department, 'QUARANTINE')
                if not scope.owner:
                    raise Denied('OWNER_APPROVAL_REQUIRED')
            self.coordination._decision(conn, context, decision_id, 'SUBTREE_QUARANTINE',
                                        dict(root_job_id=root_job_id, affected_jobs=[j[0] for j in jobs]), now)
            for job_id, _ in jobs:
                conn.execute("UPDATE jobs SET state='BLOCKED',version=version+1 WHERE job_id=? AND state NOT IN ('DONE','CANCELLED')", (job_id,))
            return [j[0] for j in jobs]
