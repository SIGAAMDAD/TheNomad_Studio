"""C-backed release state. External publication is performed only by G.

Approval consumption, effect intent and release updates use the same APSW writer.
A remote write is never treated as successful until verified readback is recorded.
"""
from __future__ import annotations

import hashlib
import json

from studio.app.errors import StudioError
from studio.app.releases import EffectRecord, ReleaseRecord
from studio.workforce import WorkforceFault

from .workforce import WorkforceStorage


class ReleaseAPSWStore:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("real APSW writer required for release state")
        self.writer = writer

    @staticmethod
    def _release(row: dict | None) -> ReleaseRecord | None:
        return ReleaseRecord(**row) if row is not None else None

    @staticmethod
    def _effect(row: dict | None) -> EffectRecord | None:
        return EffectRecord(**row) if row is not None else None

    @staticmethod
    def _raise_fault(error: WorkforceFault) -> StudioError:
        code = {"unknown": "unknown_effect", "conflict": "conflict",
                "forbidden": "forbidden", "invalid": "invalid"}.get(error.code, "internal")
        return StudioError(code, str(error))

    def prepare_release(self, *, project_id: str, principal_id: str,
                        request_id: str, request_body_sha256: str,
                        source_revision_id: str, snapshot_id: str,
                        artifact_ids: tuple[str, ...], release_digest: str) -> ReleaseRecord:
        if not artifact_ids or len(set(artifact_ids)) != len(artifact_ids):
            raise StudioError("invalid", "release artifacts must be nonempty and unique")
        release_id = "release_" + hashlib.sha256(
            (project_id + "\0" + request_id).encode("utf-8")).hexdigest()[:40]

        def action(tx):
            if tx.one("SELECT 1 AS ok FROM project_grants WHERE project_id=? AND principal_id=?",
                      (project_id, principal_id)) is None:
                raise StudioError("forbidden", "requesting principal lacks project grant")
            source = tx.one("SELECT state FROM source_revisions WHERE project_id=? AND revision_id=?",
                            (project_id, source_revision_id))
            if source is None or source["state"] != "canonical":
                raise StudioError("stale_revision", "release source is not current canonical revision")
            if tx.one("SELECT 1 AS ok FROM canon_snapshots WHERE project_id=? AND snapshot_id=?",
                      (project_id, snapshot_id)) is None:
                raise StudioError("stale_snapshot", "release snapshot not found")
            for artifact_id in artifact_ids:
                record = tx.one(
                    "SELECT source_revision_id,snapshot_id FROM artifacts "
                    "WHERE project_id=? AND artifact_id=?", (project_id, artifact_id))
                if record is None or record["source_revision_id"] != source_revision_id or record["snapshot_id"] != snapshot_id:
                    raise StudioError("invalid", "release artifact source or snapshot mismatch")
            if tx.change("INSERT INTO releases(project_id,release_id,source_revision_id,snapshot_id,"
                         "release_digest,state) VALUES(?,?,?,?,?,'prepared')",
                         (project_id,release_id,source_revision_id,snapshot_id,release_digest)) != 1:
                raise StudioError("conflict", "release insert failed")
            for artifact_id in artifact_ids:
                tx.change("INSERT INTO release_artifacts(project_id,release_id,artifact_id) VALUES(?,?,?)",
                          (project_id,release_id,artifact_id))
            self._audit(tx,project_id,principal_id,"release.prepared",release_id,request_body_sha256)
            return {"project_id":project_id,"release_id":release_id,
                    "source_revision_id":source_revision_id,
                    "snapshot_id":snapshot_id,"release_digest":release_digest,"state":"prepared"}

        # Bind every immutable command field to durable replay identity, even
        # when a trusted caller accidentally reuses a precomputed request hash.
        guard = hashlib.sha256(json.dumps(
            (request_body_sha256,principal_id,source_revision_id,snapshot_id,
             artifact_ids,release_digest),separators=(",",":")).encode()).hexdigest()
        try:
            return ReleaseRecord(**self.writer.command(
                project_id,"M.prepareRelease",request_id,guard,action))
        except WorkforceFault as error:
            raise self._raise_fault(error) from error

    def get_release(self, *, project_id: str, release_id: str) -> ReleaseRecord | None:
        row = self.writer.read(project_id,lambda tx:tx.one(
            "SELECT project_id,release_id,source_revision_id,snapshot_id,release_digest,state "
            "FROM releases WHERE project_id=? AND release_id=?", (project_id,release_id)))
        return self._release(row)

    def list_releases(self, *, project_id: str) -> list[ReleaseRecord]:
        rows = self.writer.read(project_id,lambda tx:tx.all(
            "SELECT project_id,release_id,source_revision_id,snapshot_id,release_digest,state "
            "FROM releases WHERE project_id=? ORDER BY release_id LIMIT 1000", (project_id,)))
        return [ReleaseRecord(**row) for row in rows]

    def prepare_publication(self, *, project_id: str, release_id: str,
                            decision_id: str, effect_digest: str,
                            request_id: str, request_body_sha256: str) -> EffectRecord:
        effect_id = "release_effect_" + hashlib.sha256(
            (project_id + "\0" + release_id + "\0" + request_id).encode("utf-8")).hexdigest()[:40]

        def action(tx):
            release = tx.one("SELECT release_digest,snapshot_id,state FROM releases "
                             "WHERE project_id=? AND release_id=?", (project_id,release_id))
            if release is None:
                raise StudioError("missing", "release not found")
            if release["state"] not in ("prepared", "unknown"):
                raise StudioError("conflict", "release cannot prepare publication")
            if release["release_digest"] != effect_digest:
                raise StudioError("conflict", "effect digest must equal approved release digest")
            owner = tx.one(
                "SELECT verified_owner_principal_id FROM owner_decisions "
                "WHERE project_id=? AND decision_id=? AND subject_type='release' "
                "AND subject_id=? AND action='release.publish' AND effect_digest=? "
                "AND snapshot_id=? AND state='approved'",
                (project_id,decision_id,release_id,effect_digest,release["snapshot_id"]))
            if owner is None:
                raise StudioError("requires_owner", "exact owner decision not verified")
            tx.change("INSERT INTO effect_intents(project_id,effect_id,subject_type,subject_id,action,"
                      "effect_digest,snapshot_id,decision_id,approval_required,state,"
                      "provider_key,request_id) VALUES(?,?,'release',?,'release.publish',"
                      "?,?,?,1,'prepared','github',?)",
                      (project_id,effect_id,release_id,effect_digest,release["snapshot_id"],
                       decision_id,"M.release.publish."+request_id))
            tx.change("INSERT INTO approval_consumptions(project_id,decision_id,effect_id,consumed_at) "
                      "VALUES(?,?,?,datetime('now'))",(project_id,decision_id,effect_id))
            tx.change("UPDATE releases SET decision_id=? WHERE project_id=? AND release_id=?",
                      (decision_id,project_id,release_id))
            self._audit(tx,project_id,owner["verified_owner_principal_id"],
                        "release.publication_prepared",release_id,request_body_sha256)
            return {"effect_id":effect_id,"project_id":project_id,
                    "release_id":release_id,"effect_digest":effect_digest,"state":"prepared"}

        guard = hashlib.sha256(json.dumps(
            (request_body_sha256,release_id,decision_id,effect_digest),
            separators=(",",":")).encode()).hexdigest()
        try:
            return EffectRecord(**self.writer.command(
                project_id,"M.publishRelease",request_id,guard,action))
        except WorkforceFault as error:
            raise self._raise_fault(error) from error

    def observe_publication(self, *, project_id: str, effect_id: str,
                            verified_remote_digest: str) -> None:
        with self.writer._transaction(write=True) as tx:
            row = tx.one("SELECT subject_id,effect_digest,state,decision_id FROM effect_intents "
                         "WHERE project_id=? AND effect_id=? AND subject_type='release'",
                         (project_id,effect_id))
            if row is None or row["effect_digest"] != verified_remote_digest:
                raise StudioError("conflict", "observed remote release digest differs from intent")
            if row["state"] not in ("prepared","dispatched","unknown","observed"):
                raise StudioError("conflict", "effect cannot be observed")
            tx.change("UPDATE effect_intents SET state='observed' WHERE project_id=? AND effect_id=?",
                      (project_id,effect_id))
            updated = tx.change(
                "UPDATE releases SET state='published' WHERE project_id=? AND release_id=? "
                "AND decision_id=? AND state IN ('prepared','unknown','published')",
                (project_id,row["subject_id"],row["decision_id"]))
            if updated != 1:
                raise StudioError("conflict", "release publication state mismatch")

    def mark_publication_unknown(self, *, project_id: str, effect_id: str) -> None:
        with self.writer._transaction(write=True) as tx:
            row = tx.one("SELECT subject_id,state FROM effect_intents "
                         "WHERE project_id=? AND effect_id=? AND subject_type='release'",
                         (project_id,effect_id))
            if row is None:
                raise StudioError("missing", "release publication effect absent")
            if row["state"] == "observed":
                return
            if row["state"] not in ("prepared","dispatched","unknown"):
                raise StudioError("conflict","cannot mark terminal effect unknown")
            tx.change("UPDATE effect_intents SET state='unknown' WHERE project_id=? AND effect_id=?",
                      (project_id,effect_id))
            tx.change("UPDATE releases SET state='unknown' "
                      "WHERE project_id=? AND release_id=? AND state<>'published'",
                      (project_id,row["subject_id"]))

    @staticmethod
    def _audit(tx, project_id: str, principal_id: str,
               event_type: str, subject_id: str, payload_digest: str) -> None:
        number = tx.one(
            "SELECT COALESCE(MAX(event_seq),0)+1 AS n FROM audit_events WHERE project_id=?",
            (project_id,))["n"]
        tx.change("INSERT INTO audit_events(project_id,event_seq,event_type,subject_type,"
                  "subject_id,payload_digest,actor_principal_id) "
                  "VALUES(?,?,?,'release',?,?,?)",
                  (project_id,number,event_type,subject_id,payload_digest,principal_id))
