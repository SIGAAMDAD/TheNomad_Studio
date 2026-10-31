"""WG-C's indexed, bounded APSW reader for J's verified audit-event API.

Only the internal J EventPageService may consume this reader. User-facing actor
authorization occurs in J on each request; this reader restricts its SQL to
one explicit project and advances by the durable integer event sequence.
"""
from __future__ import annotations

from typing import Any

from .engine import ScopedStore
from .errors import Invalid


class ProjectAuditReader:
    def __init__(self, store: ScopedStore):
        if not isinstance(store, ScopedStore):
            raise TypeError('real scoped APSW store required')
        self._store = store

    def scan_project_events(self, *, project_id: str, after: int,
                            limit: int) -> list[dict[str, Any]]:
        if not isinstance(project_id, str) or not project_id:
            raise Invalid('project required for audit page')
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise Invalid('invalid bounded event cursor/limit')
        # C owns the connection and query. The PK/index (project_id,event_seq)
        # makes cursor pagination O(log N + page) rather than a table scan.
        sql = ('SELECT project_id,event_seq,event_type,subject_id,payload_digest '
               'FROM audit_events INDEXED BY idx_events_cursor '
               'WHERE project_id=? AND event_seq>? '
               'ORDER BY event_seq ASC LIMIT ?')
        with self._store._mutex:
            cursor = self._store._conn.execute(sql, (project_id, after, limit))
            return [
                dict(zip(('project_id','event_seq','event_type',
                          'subject_id','payload_digest'), row))
                for row in cursor
            ]
