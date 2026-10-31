"""Production F verified report profile and output blob ports over G/C bytes.

The project/profile association is checked on each use. A report can only
reference an original, durable, project-bound input profile and durable output.
"""
from __future__ import annotations

from pathlib import Path

from studio.operations.core import DomainError
from .artifact_catalog import ArtifactAPSWCatalog
from .workforce import WorkforceStorage


class VerifiedReportBlobs:
    def __init__(self, writer: WorkforceStorage, root: str | Path):
        if not isinstance(writer,WorkforceStorage):
            raise TypeError("C APSW writer required for report bytes")
        self.writer=writer
        self.artifacts=ArtifactAPSWCatalog(writer,root)

    def load_verified(self, project_id: str, report_profile_digest: str) -> bytes:
        row=self.writer.read(project_id,lambda tx:tx.one(
            "SELECT 1 AS verified FROM project_blobs p JOIN blobs b "
            "ON b.content_digest=p.content_digest "
            "WHERE p.project_id=? AND p.content_digest=? AND p.purpose='report-profile' "
            "AND b.durability='verified'",
            (project_id,report_profile_digest)))
        if row is None:
            raise DomainError("forbidden","report profile was not project-verified")
        try:
            return self.artifacts.get(project_id=project_id,digest=report_profile_digest)
        except Exception as exc:
            raise DomainError("conflict","report profile original bytes unavailable") from exc

    def store_verified(self, project_id: str, content: bytes, purpose: str) -> str:
        if purpose!="report":
            raise DomainError("forbidden","F may store only verified report outputs")
        return self.artifacts.put_verified(
            project_id=project_id,content=content,purpose="report")


class VerifiedReportBlobsBuilder:
    def for_writer(self, writer: WorkforceStorage, root: str | Path) -> VerifiedReportBlobs:
        return VerifiedReportBlobs(writer,root)
