"""Real original Bible-byte and conservative task-impact ports for A governance.

The content digest alone grants nothing: every read verifies its project,
purpose, durable database record, canonical file location, size and SHA-256.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from studio.governance.model import GovernanceError
from .blobs import ContentAddressedStore
from .workforce import WorkforceStorage


class VerifiedGovernanceBlobSource:
    def __init__(self, writer: WorkforceStorage, root: str | Path):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("C APSW writer required for Bible source")
        self.writer=writer
        self.content=ContentAddressedStore(root)

    def read_verified(self, project_id: str, digest: str, purpose: str) -> bytes:
        if purpose not in ("bible-content","bible-manifest"):
            raise GovernanceError("forbidden", "unrecognized Bible byte purpose")
        row=self.writer.read(project_id,lambda tx:tx.one(
            "SELECT b.byte_size,b.store_key FROM project_blobs p "
            "JOIN blobs b ON b.content_digest=p.content_digest "
            "WHERE p.project_id=? AND p.content_digest=? AND p.purpose=? "
            "AND b.durability='verified'", (project_id,digest,purpose)))
        if row is None:
            raise GovernanceError("forbidden", "no verified project Bible blob")
        try:
            expected=self.content._file(digest).relative_to(self.content.root).as_posix()
            if row["store_key"]!=expected:
                raise GovernanceError("conflict", "Bible blob metadata points to different bytes")
            payload=self.content.read_digest(digest)
        except GovernanceError:
            raise
        except Exception as exc:
            raise GovernanceError("conflict", "Bible physical bytes unavailable") from exc
        if len(payload)!=row["byte_size"] or hashlib.sha256(payload).hexdigest()!=digest:
            raise GovernanceError("conflict", "original Bible bytes fail integrity check")
        return payload


class CanonImpactAPSWCatalog:
    """Conservatively mark all tasks pinned to published Canon for re-review.

    The current schema has no clause-to-task edge table. Returning the whole
    pinned set avoids incorrectly declaring a changed clause to have no impact.
    """
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer,WorkforceStorage):
            raise TypeError("C APSW writer required for Canon impact")
        self.writer=writer

    def referenced_tasks(self, project_id: str, clause_keys: set[str]) -> set[str]:
        if not clause_keys:
            return set()
        if any(not isinstance(x,str) or not x for x in clause_keys):
            raise GovernanceError("invalid","invalid Canon clause identifier")
        rows=self.writer.read(project_id,lambda tx:tx.all(
            "SELECT t.task_id FROM tasks t "
            "JOIN canon_snapshots c ON c.project_id=t.project_id "
            "AND c.snapshot_id=t.snapshot_id "
            "WHERE t.project_id=? ORDER BY t.task_id LIMIT 10001",(project_id,)))
        if len(rows)>10000:
            raise GovernanceError("capability_unavailable","Canon impact exceeds bounded task inventory")
        return {row["task_id"] for row in rows}
