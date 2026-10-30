"""Durable G artifact catalog and original-byte port for E/A reports.

A committed artifact references both physically verified content-addressed
bytes and project-scoped SQL blob associations. The C single writer commits
artifact, content associations and audit together; raw worker metadata never
creates an artifact without independent byte readback.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from studio.integrations.artifacts import Artifact
from studio.integrations.common import IntegrationError, identifier
from studio.workforce import WorkforceFault

from .blobs import ContentAddressedStore
from .workforce import WorkforceStorage


class ArtifactAPSWCatalog:
    def __init__(self, writer: WorkforceStorage, root: str | Path):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("real C APSW writer required")
        self.writer=writer
        self.bytes=ContentAddressedStore(root)

    def put_verified(self, *, project_id: str, content: bytes,
                     purpose: str="evidence") -> str:
        identifier(project_id)
        identifier(purpose)
        stored=self.bytes.put(content)
        self.bytes.read_digest(stored.digest)
        request_id="blob."+purpose+"."+stored.digest
        def commit(tx):
            row=tx.one("SELECT byte_size,store_key,durability FROM blobs WHERE content_digest=?",
                       (stored.digest,))
            if row is not None and (row["byte_size"]!=stored.byte_size
                                    or row["store_key"]!=stored.store_key
                                    or row["durability"]!="verified"):
                raise IntegrationError("conflict","global blob identity differs from original bytes")
            if row is None:
                tx.change(
                    "INSERT INTO blobs(content_digest,byte_size,store_key,durability)"
                    "VALUES(?,?,?,'verified')",
                    (stored.digest,stored.byte_size,stored.store_key))
            tx.change("INSERT OR IGNORE INTO project_blobs(project_id,content_digest,purpose) "
                      "VALUES(?,?,?)",(project_id,stored.digest,purpose))
            return {"content_digest":stored.digest}
        try:
            self.writer.command(project_id,"G.storeVerifiedBlob",request_id,
                                stored.digest,commit)
        except WorkforceFault as exc:
            raise IntegrationError({"conflict":"conflict","invalid":"invalid",
                                    "unknown":"unknown_effect"}.get(exc.code,"internal"),
                                   str(exc)) from exc
        return stored.digest

    def get(self, *, project_id: str, digest: str) -> bytes:
        identifier(project_id)
        if not isinstance(digest,str) or len(digest)!=64:
            raise IntegrationError("invalid","artifact digest invalid")
        row=self.writer.read(project_id,lambda tx:tx.one(
            "SELECT b.byte_size,b.store_key FROM blobs b "
            "JOIN project_blobs p ON p.content_digest=b.content_digest "
            "WHERE p.project_id=? AND p.content_digest=? AND b.durability='verified' "
            "ORDER BY p.purpose LIMIT 1",(project_id,digest)))
        if row is None:
            raise IntegrationError("forbidden","original bytes not shared with this project")
        expected=self.bytes._file(digest).relative_to(self.bytes.root).as_posix()
        if expected!=row["store_key"]:
            raise IntegrationError("conflict","artifact bytes metadata do not match disk path")
        try:
            body=self.bytes.read_digest(digest)
        except Exception as exc:
            raise IntegrationError("conflict","artifact original bytes absent or corrupt") from exc
        if len(body)!=row["byte_size"] or hashlib.sha256(body).hexdigest()!=digest:
            raise IntegrationError("conflict","artifact original bytes failed verification")
        return body

    def register(self, meta: Artifact, *, validator=None) -> None:
        identifier(meta.project_id)
        identifier(meta.artifact_id)
        original=self.get(project_id=meta.project_id,digest=meta.content_digest)
        self.get(project_id=meta.project_id,digest=meta.manifest_digest)
        if validator is not None:
            validator(original)
        elif meta.validator_digest is not None:
            raise IntegrationError("invalid","declared validator did not execute")
        if meta.storage_key!="sha256:"+meta.content_digest:
            raise IntegrationError("invalid","artifact source and storage key differ")
        record=json.dumps({
            "project_id":meta.project_id,"artifact_id":meta.artifact_id,
            "kind":meta.kind,"content_digest":meta.content_digest,
            "manifest_digest":meta.manifest_digest,
            "producer_session_id":meta.producer_session_id,
            "source_revision_id":meta.source_revision_id,
            "snapshot_id":meta.canon_snapshot_id},sort_keys=True,separators=(",",":"))
        body_digest=hashlib.sha256(record.encode("utf-8")).hexdigest()

        def save(tx):
            previous=tx.one(
                "SELECT kind,content_digest,manifest_digest,producer_session_id,"
                "source_revision_id,snapshot_id FROM artifacts "
                "WHERE project_id=? AND artifact_id=?",
                (meta.project_id,meta.artifact_id))
            expected=dict(kind=meta.kind,content_digest=meta.content_digest,
                          manifest_digest=meta.manifest_digest,
                          producer_session_id=meta.producer_session_id,
                          source_revision_id=meta.source_revision_id,
                          snapshot_id=meta.canon_snapshot_id)
            if previous is not None:
                if previous!=expected:
                    raise IntegrationError("conflict","immutable artifact metadata changed")
                return {"artifact_id":meta.artifact_id}
            tx.change(
                "INSERT INTO artifacts(project_id,artifact_id,kind,content_digest,"
                "manifest_digest,producer_session_id,source_revision_id,snapshot_id)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (meta.project_id,meta.artifact_id,meta.kind,meta.content_digest,
                 meta.manifest_digest,meta.producer_session_id,
                 meta.source_revision_id,meta.canon_snapshot_id))
            return {"artifact_id":meta.artifact_id}
        try:
            self.writer.command(meta.project_id,"G.registerArtifact",
                                "artifact."+meta.artifact_id,body_digest,save)
        except WorkforceFault as exc:
            raise IntegrationError(
                {"conflict":"conflict","invalid":"invalid",
                 "unknown":"unknown_effect"}.get(exc.code,"internal"),str(exc)) from exc

    def get_artifact(self, *, project_id: str, artifact_id: str) -> Artifact:
        identifier(project_id)
        identifier(artifact_id)
        row=self.writer.read(project_id,lambda tx:tx.one(
            "SELECT project_id,artifact_id,kind,content_digest,manifest_digest,"
            "producer_session_id,source_revision_id,snapshot_id FROM artifacts "
            "WHERE project_id=? AND artifact_id=?",(project_id,artifact_id)))
        if row is None:
            raise IntegrationError("missing","project artifact unavailable")
        self.get(project_id=project_id,digest=row["content_digest"])
        self.get(project_id=project_id,digest=row["manifest_digest"])
        return Artifact(row["project_id"],row["artifact_id"],row["kind"],
                        row["content_digest"],row["manifest_digest"],
                        row["producer_session_id"],row["source_revision_id"],
                        row["snapshot_id"],None,"sha256:"+row["content_digest"])

    def list_artifacts(self, *, project_id: str) -> list[dict[str,str]]:
        identifier(project_id)
        rows=self.writer.read(project_id,lambda tx:tx.all(
            "SELECT artifact_id FROM artifacts WHERE project_id=? ORDER BY artifact_id LIMIT 10001",
            (project_id,)))
        if len(rows)>10000:
            raise IntegrationError("capability_unavailable","artifact count exceeds bounded page")
        return [self.get_artifact(project_id=project_id,
                                  artifact_id=row["artifact_id"]).summary() for row in rows]
