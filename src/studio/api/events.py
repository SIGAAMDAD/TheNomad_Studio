"""Durable per-project audit cursor, sourced only from WG-C's named SQL reader."""
from __future__ import annotations

from typing import Mapping, Protocol, Sequence

from .errors import ApiError


class AuditEventReader(Protocol):
    """Return ascending committed audit_events after `after`, scoped to project.

    Implemented by WG-C's APSW named query on the (project_id,event_seq)
    index. Never synthesize events or use an in-memory counter in production.
    """
    def scan_project_events(self, *, project_id: str, after: int, limit: int) -> Sequence[Mapping]: ...


class EventPageService:
    def __init__(self, reader: AuditEventReader):
        self._reader = reader

    def listEvents(self, actor: dict, project_id: str, after: int | None = None,
                   limit: int | None = None) -> dict:
        if project_id not in actor['allowed_project_ids']:
            raise ApiError('forbidden', 'project unavailable')
        offset = 0 if after is None else after
        page_size = 100 if limit is None else limit
        if type(offset) is not int or offset < 0 or type(page_size) is not int or not 1 <= page_size <= 500:
            raise ApiError('invalid', 'invalid event pagination')
        rows = self._reader.scan_project_events(project_id=project_id, after=offset, limit=page_size)
        if len(rows) > page_size:
            raise ApiError('internal', 'reader exceeded bounded event page')
        events = []
        cursor = offset
        for row in rows:
            seq = row.get('event_seq')
            if (row.get('project_id') != project_id or type(seq) is not int or seq <= cursor
                    or not isinstance(row.get('event_type'), str) or not row['event_type']
                    or not isinstance(row.get('payload_digest'), str) or not row['payload_digest']
                    or (row.get('subject_id') is not None and not isinstance(row['subject_id'], str))):
                raise ApiError('internal', 'reader returned untrusted or unordered event')
            events.append({
                'project_id': project_id, 'event_seq': seq, 'event_type': row['event_type'],
                'subject_id': row.get('subject_id'), 'payload_digest': row['payload_digest'],
            })
            cursor = seq
        return {'events': events, 'next_cursor': cursor}
