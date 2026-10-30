"""Read current persisted project grants at the authenticated API boundary."""
from __future__ import annotations

from .workforce import WorkforceStorage


class CurrentGrants:
    def __init__(self, storage: WorkforceStorage):
        if not isinstance(storage, WorkforceStorage):
            raise TypeError('APSW storage required')
        self.storage = storage

    def granted_project_ids(self, principal_id: str) -> tuple[str, ...]:
        if not isinstance(principal_id, str) or not principal_id:
            return ()
        with self.storage._lock:
            rows = self.storage._connection.execute(
                "SELECT DISTINCT g.project_id FROM project_grants g "
                "JOIN projects p ON p.project_id=g.project_id "
                "WHERE g.principal_id=? AND p.state<>'archived' "
                "ORDER BY g.project_id", (principal_id,))
            return tuple(row[0] for row in rows)
