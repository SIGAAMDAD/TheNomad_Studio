"""C-owned current tool grants and shift/lease authorization for private ToolHost."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from studio.storage.workforce import WorkforceStorage
from .toolhost_broker import PrivateToolHostBroker


class CurrentToolHostAuthorizer:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("current APSW workforce writer required")
        self.writer = writer

    def __call__(self, frame: Mapping[str, object]) -> bool:
        project, operation = frame.get("project_id"), frame.get("operation_id")
        kind = frame.get("kind")
        if kind not in ("run", "observe", "cancel") or not all(
                isinstance(x, str) and x for x in (project, operation)):
            return False

        def verify(tx):
            state = tx.one(
                "SELECT o.state AS operation_state, o.shift_id, o.lease_generation, "
                "o.entrypoint_id, g.grant_digest, g.package_digest, g.workspace_id, "
                "g.revoked, p.enabled, sh.state AS shift_state, sh.worker_id "
                "FROM tool_operations o "
                "JOIN tool_grants g ON g.project_id=o.project_id AND g.grant_id=o.grant_id "
                "JOIN project_packages p ON p.project_id=g.project_id "
                "AND p.package_digest=g.package_digest "
                "JOIN worker_shifts sh ON sh.project_id=o.project_id "
                "AND sh.shift_id=o.shift_id "
                "WHERE o.project_id=? AND o.operation_id=?", (project, operation))
            if (state is None or state["revoked"] != 0 or state["enabled"] != 1):
                return False
            if kind == "observe":
                return True
            if (state["lease_generation"] != frame.get("lease_generation")
                    or state["operation_state"] not in ("prepared", "running")):
                return False
            if kind == "cancel":
                return True
            if (state["operation_state"] != "prepared" or state["shift_state"] != "active"
                    or any(state[field] != frame.get(key) for field, key in (
                        ("shift_id", "shift_id"), ("worker_id", "worker_id"),
                        ("entrypoint_id", "entrypoint_id"),
                        ("grant_digest", "grant_digest"),
                        ("package_digest", "package_digest"),
                        ("workspace_id", "workspace_id")))):
                return False
            lease = tx.one(
                "SELECT l.expires_at FROM shift_assignments a "
                "JOIN leases l ON l.project_id=a.project_id AND l.task_id=a.task_id "
                "AND l.generation=a.lease_generation AND l.worker_id=a.worker_id "
                "JOIN tasks t ON t.project_id=a.project_id AND t.task_id=a.task_id "
                "JOIN source_revisions s ON s.project_id=a.project_id "
                "AND s.revision_id=? AND s.state='canonical' "
                "WHERE a.project_id=? AND a.task_id=? AND a.shift_id=? "
                "AND a.worker_id=? AND a.lease_generation=? "
                "AND a.state IN ('assigned','observed') AND l.status='active' "
                "AND t.state IN ('leased','running') AND t.snapshot_id=?",
                (frame.get("source_revision_id"), project, frame.get("task_id"),
                 state["shift_id"], state["worker_id"], state["lease_generation"],
                 frame.get("canon_snapshot_id")))
            if lease is None:
                return False
            try:
                expiry = datetime.fromisoformat(lease["expires_at"].replace("Z", "+00:00"))
                return expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
            except (AttributeError, TypeError, ValueError):
                return False

        return bool(self.writer.read(project, verify))


class ToolHostBrokerBuilder:
    """Construct one SHA-pinned broker against the actual production C writer."""
    def __init__(self, *, dotnet: Path, host_dll: Path, host_sha256: str,
                 registry: Path, registry_sha256: str, journal: Path,
                 expected_isolation: str):
        if not isinstance(expected_isolation, str) or not expected_isolation:
            raise ValueError("approved sandbox capability required")
        self.config = dict(dotnet=Path(dotnet), host_dll=Path(host_dll),
                           host_sha256=host_sha256, registry=Path(registry),
                           registry_sha256=registry_sha256, journal=Path(journal),
                           expected_isolation=expected_isolation)

    def for_writer(self, writer: WorkforceStorage, _root: Path) -> PrivateToolHostBroker:
        return PrivateToolHostBroker(**self.config,
                                     authorize=CurrentToolHostAuthorizer(writer))
