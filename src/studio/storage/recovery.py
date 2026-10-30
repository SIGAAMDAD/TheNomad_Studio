"""C-backed restore request journal and verified-backup gates.

Requesting recovery is not executing a restore. The recorded job is committed
before an authorized, separately verified recovery executor may act on it.
"""
from __future__ import annotations

import hashlib

from studio.app.errors import StudioError
from studio.workforce import WorkforceFault
from .workforce import WorkforceStorage


class RecoveryAPSWStore:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("production recovery requires a real APSW writer")
        self.writer = writer

    def project_revision(self, *, project_id: str) -> int:
        value = self.writer.read(project_id, lambda tx: tx.one(
            "SELECT revision FROM projects WHERE project_id=?", (project_id,)))
        if value is None:
            raise StudioError("missing", "project does not exist")
        return value["revision"]

    def backup_is_verified(self, *, project_id: str, backup_digest: str) -> bool:
        row = self.writer.read(project_id,lambda tx: tx.one(
            "SELECT 1 AS verified FROM backup_sets b "
            "JOIN blobs db ON db.content_digest=b.database_blob_digest "
            "JOIN blobs mb ON mb.content_digest=b.manifest_digest "
            "JOIN project_blobs pd ON pd.project_id=b.project_id "
            "AND pd.content_digest=b.database_blob_digest "
            "JOIN project_blobs pm ON pm.project_id=b.project_id "
            "AND pm.content_digest=b.manifest_digest "
            "WHERE b.project_id=? AND b.backup_digest=? AND b.state='verified' "
            "AND db.durability='verified' AND mb.durability='verified' LIMIT 1",
            (project_id,backup_digest)))
        return row is not None

    def outstanding_uncertain_effects(self, *, project_id: str) -> list[str]:
        rows = self.writer.read(project_id, lambda tx:tx.all(
            "SELECT effect_id FROM effect_intents WHERE project_id=? "
            "AND state IN ('unknown','dispatched') ORDER BY effect_id LIMIT 501",
            (project_id,)))
        if len(rows) > 500:
            raise StudioError("capability_unavailable", "outstanding effects exceed bounded review")
        return [row["effect_id"] for row in rows]

    def request_restore(self, *, project_id: str, principal_id: str,
                        request_id: str, request_body_sha256: str,
                        backup_digest: str, expected_revision: int) -> tuple[str, str]:
        if type(expected_revision) is not int or expected_revision < 1:
            raise StudioError("invalid", "restore revision must be positive")
        recovery_id = "restore_" + hashlib.sha256(
            (project_id + "\0" + request_id).encode()).hexdigest()[:40]

        def action(tx):
            grant = tx.one(
                "SELECT 1 AS ok FROM project_grants g "
                "JOIN principals p ON p.principal_id=g.principal_id "
                "WHERE g.project_id=? AND g.principal_id=? AND g.grant_code='owner' "
                "AND p.kind='human' LIMIT 1", (project_id,principal_id))
            if grant is None:
                raise StudioError("requires_owner", "verified human project owner required")
            current = tx.one("SELECT revision FROM projects WHERE project_id=?",
                             (project_id,))
            if current is None or current["revision"] != expected_revision:
                raise StudioError("stale_revision", "project revision advanced")
            backup = tx.one(
                "SELECT b.state,db.durability AS database_state,mb.durability AS manifest_state "
                "FROM backup_sets b JOIN blobs db ON db.content_digest=b.database_blob_digest "
                "JOIN blobs mb ON mb.content_digest=b.manifest_digest "
                "WHERE b.project_id=? AND b.backup_digest=?",
                (project_id,backup_digest))
            if (backup is None or backup["state"] != "verified"
                    or backup["database_state"] != "verified"
                    or backup["manifest_state"] != "verified"):
                raise StudioError("invalid", "backup is not durable and verified")
            unsettled = tx.one(
                "SELECT effect_id FROM effect_intents WHERE project_id=? "
                "AND state IN ('unknown','dispatched') LIMIT 1", (project_id,))
            if unsettled is not None:
                raise StudioError("unknown_effect", "remote provider effect requires reconciliation")
            tx.change(
                "INSERT INTO recovery_jobs(project_id,recovery_id,backup_digest,"
                "expected_project_revision,state,created_at) "
                "VALUES(?,?,?,?,'requested',datetime('now'))",
                (project_id,recovery_id,backup_digest,expected_revision))
            seq = tx.one(
                "SELECT COALESCE(MAX(event_seq),0)+1 AS n FROM audit_events WHERE project_id=?",
                (project_id,))["n"]
            tx.change(
                "INSERT INTO audit_events(project_id,event_seq,event_type,subject_type,"
                "subject_id,payload_digest,actor_principal_id) "
                "VALUES(?,?,'recovery.requested','recovery',?,?,?)",
                (project_id,seq,recovery_id,request_body_sha256,principal_id))
            return {"recovery_id":recovery_id,"state":"requested"}

        try:
            result = self.writer.command(project_id,"M.requestRestore",request_id,
                                         request_body_sha256,action)
        except WorkforceFault as exc:
            raise StudioError({"unknown":"unknown_effect","conflict":"conflict",
                               "forbidden":"forbidden","invalid":"invalid"}.get(exc.code,"internal"),
                              str(exc)) from exc
        return result["recovery_id"], result["state"]

    def recovery_state(self, *, project_id: str) -> str:
        row = self.writer.read(project_id,lambda tx:tx.one(
            "SELECT state FROM recovery_jobs WHERE project_id=? "
            "ORDER BY created_at DESC,recovery_id DESC LIMIT 1", (project_id,)))
        return row["state"] if row is not None else "idle"
