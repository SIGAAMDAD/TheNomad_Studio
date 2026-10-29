"""R9 fenced task attempt helpers, CAB-C writer tables only.

A shift is a durable task attempt identified by (task_id, lease_epoch), not a
self-reported chat turn. This module is used by SchedulingPort.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4
from nomad_studio.production.workgraph.core import Refused, require


def active_lease(conn: Any, task_id: str, epoch: int, now: int) -> dict:
    """Fence check inside same serialized writer transaction as any attempt mutation."""
    row=conn.execute('SELECT * FROM v1_worker_leases WHERE task_id=? ORDER BY epoch DESC LIMIT 1',
                     (task_id,)).fetchone()
    require(row is not None,'LEASE_FENCED','CONFLICT')
    lease=dict(row)
    require(lease['epoch']==epoch and lease['fenced']==0 and lease['expires_at_us']>now,
            'LEASE_FENCED','CONFLICT')
    return lease


def begin_attempt(conn: Any, task_id: str, worker_id: str, now: int,
                  ttl_us: int, input_digest: str, session_id: str) -> dict:
    row=conn.execute('SELECT MAX(epoch) FROM v1_worker_leases WHERE task_id=?',(task_id,)).fetchone()
    epoch=(row[0] or 0)+1
    lease_id='lease:'+uuid4().hex
    attempt_id='attempt:'+uuid4().hex
    conn.execute('INSERT INTO v1_worker_leases(lease_id,task_id,worker_id,session_id,epoch,expires_at_us,fenced) VALUES(?,?,?,?,?,?,0)',
                 (lease_id,task_id,worker_id,session_id,epoch,now+ttl_us))
    conn.execute('INSERT INTO v1_task_attempts(attempt_id,task_id,lease_epoch,input_digest,status,started_us) VALUES(?,?,?,?,?,?)',
                 (attempt_id,task_id,epoch,input_digest,'ACTIVE',now))
    return {'lease_id':lease_id,'task_id':task_id,'worker_principal':worker_id,
            'session_id':session_id,'lease_epoch':epoch,'expires_at_us':now+ttl_us,
            'attempt_id':attempt_id,'input_digest':input_digest}


def close_attempt(conn: Any, task_id: str, epoch: int, status: str,
                  now: int, result_digest: str | None) -> None:
    require(status in ('SUCCEEDED','FAILED_TRANSIENT','FAILED_SEMANTIC','FENCED'))
    cursor=conn.execute('UPDATE v1_task_attempts SET status=?,result_digest=?,finished_us=? WHERE task_id=? AND lease_epoch=? AND status=\'ACTIVE\'',
                        (status,result_digest,now,task_id,epoch))
    require(cursor.rowcount==1,'LEASE_FENCED','CONFLICT')
    conn.execute('UPDATE v1_worker_leases SET fenced=1 WHERE task_id=? AND epoch=?',(task_id,epoch))
