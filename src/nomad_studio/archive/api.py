"""Auditor-owned archive workflow using only frozen StateStore/Component ports.

The archive service never opens the live SQLite DB and cannot authorize deletion,
retention, live writer cutover, or independent witness receipts itself.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from typing import Any, Iterator, Mapping
from uuid import uuid4

from nomad_studio.contracts.v1 import (CommandEnvelope, DomainEvent, Outcome,
                                       QueryEnvelope, VerifiedIdentity)
from .format import (ArchiveError, build_database, canonical, compress_database,
                     digest, file_digest, fsync_dir, inspect_archive, integrity_check)

_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_REF = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_./:@+~-]{0,255}$")
_ALLOWED_KINDS = {"source", "receipt", "historical", "artifact", "document"}
_MAX_EVIDENCE = 10000
_MAX_EVIDENCE_BYTES = 128 * 1024 * 1024


class CommandFailure(Exception):
    def __init__(self, state: str, code: str, message: str = "") -> None:
        self.state, self.code, self.message = state, code, message
        super().__init__(message or code)


def _fail(code: str, *, state: str = "DENIED") -> None:
    raise CommandFailure(state, code)


def _require_fields(value: Mapping[str, Any], required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, Mapping) or set(value) - required - optional or not required <= set(value):
        _fail("INVALID_SCHEMA")


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail("INVALID_SCHEMA")
    return value


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _fail("INVALID_SCHEMA")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str) or not _REF.fullmatch(value):
        _fail("INVALID_SCHEMA")
    return value


def _sources(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > _MAX_EVIDENCE:
        _fail("INVALID_SCHEMA")
    result: list[dict[str, str]] = []
    for item in value:
        _require_fields(item, {"kind", "reference", "revision", "sha256"})
        if item["kind"] not in _ALLOWED_KINDS:
            _fail("INVALID_SCHEMA")
        result.append({"kind": item["kind"], "reference": _string(item["reference"]),
                       "revision": _string(item["revision"]), "sha256": _hash(item["sha256"])})
    if len({(r["kind"], r["reference"], r["revision"]) for r in result}) != len(result):
        _fail("INVALID_SCHEMA")
    return sorted(result, key=lambda r: (r["kind"], r["reference"], r["revision"]))


def _response(outcome: Outcome) -> str:
    return canonical({"state": outcome.state, "code": outcome.code, "receipt_id": outcome.receipt_id,
                      "payload": outcome.payload}).decode("ascii")


def _receipt(raw: str) -> Outcome:
    item = json.loads(raw)
    return Outcome(**item)


@dataclass
class ArchiveComponent:
    deps: Mapping[str, object]
    name: str = "archive"

    def __post_init__(self) -> None:
        configured_root = self.deps.get("archive_root")
        if not configured_root or Path(configured_root).is_symlink():
            raise ValueError("archive_root must be an explicitly configured non-symlink directory")
        self.root = Path(configured_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            stat = self.root.stat()
            if stat.st_mode & 0o077 or stat.st_uid != os.getuid():
                raise ValueError("archive_root must be privately owned and mode 0700")
        if not self.root.is_dir():
            raise ValueError("archive_root must be a directory")
        self.pending = self.root / ".pending"
        if self.pending.is_symlink():
            raise ValueError("archive staging root must not be a symlink")
        self.pending.mkdir(exist_ok=True, mode=0o700)
        self.pending.chmod(0o700)
        self.state_store = self.deps.get("state")
        if self.state_store is None:
            raise ValueError("state StateStore dependency required; migrations applied by state owner")
        self.encrypted = self.deps.get("encrypted", False)
        if not isinstance(self.encrypted, bool):
            raise ValueError("encrypted must be boolean")
        self._lock = threading.RLock()

    def _authorize(self, identity: VerifiedIdentity, scope: str) -> None:
        if not isinstance(identity, VerifiedIdentity) or not identity.principal_id or scope not in identity.scopes:
            _fail("INVALID_SCOPE")
        if identity.expires_at_us <= time.time_ns() // 1000:
            _fail("EXPIRED_GRANT")
        authority = self.deps.get("authority")
        if authority is None:
            _fail("NOT_CONFIGURED", state="BLOCKED")
        if not authority.valid(identity):
            _fail("REVOKED_GRANT")

    def _job(self, archive_id: str) -> dict[str, Any]:
        row = self.state_store.read(lambda rd: rd.fetch_one("SELECT * FROM archive_jobs WHERE archive_id=?", (archive_id,)))
        if row is None:
            _fail("INVALID_SCHEMA")
        return dict(row)

    def _dir(self, archive_id: str) -> Path:
        return self.pending / archive_id

    def _final(self, archive_id: str) -> Path:
        return self.root / (archive_id + (".sqlite.gz.enc" if self.encrypted else ".sqlite.gz"))

    def _event(self, tx: Any, command: CommandEnvelope, actor: VerifiedIdentity, kind: str, payload: Mapping[str, Any]) -> str:
        event_id = uuid4().hex
        evt = DomainEvent(event_id=event_id, domain="archive", kind=kind,
                          actor_principal_id=actor.principal_id, issued_at_us=time.time_ns() // 1000,
                          correlation_id=command.request_id, job_id=command.job_id,
                          shift_id=command.shift_id, assignment_epoch=command.assignment_epoch,
                          payload_sha256=digest(canonical(payload)), payload=payload)
        tx.append_event(evt)
        return event_id

    @staticmethod
    def _fingerprint(command: CommandEnvelope, identity: VerifiedIdentity) -> str:
        return digest(canonical({"version": command.version,
                                 "domain": command.domain, "operation": command.operation,
                                 "job_id": command.job_id, "payload": command.payload,
                                 "assignment_epoch": command.assignment_epoch,
                                 "source_sha": command.source_sha,
                                 "principal_id": identity.principal_id,
                                 "authority_epoch": identity.current_authority_epoch}))

    def _commit(self, command: CommandEnvelope, identity: VerifiedIdentity, fn: Any) -> Outcome:
        # Recheck after potentially long offline snapshot/compression/I/O, before SQL.
        # Never invoke a network/provider adapter inside the SQLite transaction.
        self._authorize(identity, "archive:witness" if command.operation == "archive.restore_proof" else "archive:auditor")
        fingerprint = self._fingerprint(command, identity)
        def transact(tx: Any) -> Outcome:
            previous = tx.fetch_one("SELECT command_sha256,response_json FROM archive_idempotency WHERE key=?",
                                    (command.idempotency_key,))
            if previous:
                if previous["command_sha256"] != fingerprint:
                    _fail("IDEMPOTENCY_CONFLICT", state="CONFLICT")
                return _receipt(previous["response_json"])
            outcome = fn(tx)
            tx.execute("INSERT INTO archive_idempotency(key,command_sha256,response_json) VALUES (?,?,?)",
                       (command.idempotency_key, fingerprint, _response(outcome)))
            return outcome
        return self.state_store.transact(transact, idempotency_key=None)

    def execute(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        try:
            if not isinstance(command, CommandEnvelope) or command.version != "NOMAD/CMD/1" or command.domain != "archive" or command.operation not in {
                    "archive.open_job", "archive.snapshot", "archive.compress", "archive.seal", "archive.restore_proof"}:
                _fail("MALFORMED_REQUEST")
            if not isinstance(command.idempotency_key, str) or not command.idempotency_key or len(command.idempotency_key) > 200:
                _fail("INVALID_SCHEMA")
            if not isinstance(command.request_id, str) or not command.request_id:
                _fail("INVALID_SCHEMA")
            with self._lock:
                if command.operation == "archive.restore_proof":
                    self._authorize(identity, "archive:witness")
                else:
                    self._authorize(identity, "archive:auditor")
                # A finished request must not perform filesystem/network operations again.
                fingerprint = self._fingerprint(command, identity)
                old = self.state_store.read(lambda rd: rd.fetch_one("SELECT command_sha256,response_json FROM archive_idempotency WHERE key=?", (command.idempotency_key,)))
                if old:
                    if old["command_sha256"] != fingerprint:
                        _fail("IDEMPOTENCY_CONFLICT", state="CONFLICT")
                    return _receipt(old["response_json"])
                method = getattr(self, "_" + command.operation.split(".")[1])
                return method(command, identity)
        except CommandFailure as e:
            return Outcome(state=e.state, code=e.code)
        except (ArchiveError, ValueError, KeyError, TypeError, OSError) as e:
            return Outcome(state="DENIED", code="INVALID_SCHEMA", payload={"reason": type(e).__name__})

    def _open_job(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        _require_fields(command.payload, {"archive_id", "approval_ref", "evidence_refs"})
        aid = _identifier(command.payload["archive_id"])
        approval_ref = _string(command.payload["approval_ref"])
        job_id = _identifier(command.job_id)
        evidence = _sources(command.payload["evidence_refs"])
        approval = self.deps.get("approvals")
        if approval is None:
            _fail("NOT_CONFIGURED", state="BLOCKED")
        if not approval.approved(job_id=job_id, approval_ref=approval_ref,
                                 principal_id=identity.principal_id, evidence_refs=evidence):
            _fail("OWNER_APPROVAL_REQUIRED")
        existing = self.state_store.read(lambda rd: rd.fetch_one(
            "SELECT archive_id FROM archive_jobs WHERE archive_id=? OR job_id=?", (aid, job_id)))
        if existing:
            _fail("IDEMPOTENCY_CONFLICT", state="CONFLICT")
        def apply(tx: Any) -> Outcome:
            tx.execute("INSERT INTO archive_jobs (archive_id,job_id,approved_ref,created_by,evidence_json,state,encrypted) VALUES (?,?,?,?,?,?,?)",
                       (aid, job_id, approval_ref, identity.principal_id, canonical(evidence).decode("ascii"), "OPEN", int(self.encrypted)))
            rid = self._event(tx, command, identity, "ArchiveJobApproved", {"archive_id": aid, "approval_ref": approval_ref})
            return Outcome(state="ACCEPTED", code="OPEN", receipt_id=rid, payload={"archive_id": aid, "status": "OPEN"})
        return self._commit(command, identity, apply)

    def _controlled_job(self, command: CommandEnvelope, identity: VerifiedIdentity) -> tuple[str, dict[str, Any]]:
        _require_fields(command.payload, {"archive_id"})
        aid = _identifier(command.payload["archive_id"])
        job = self._job(aid)
        if command.job_id != job["job_id"]:
            _fail("INVALID_SCOPE")
        if job["created_by"] != identity.principal_id:
            _fail("INVALID_SCOPE")
        if bool(job["encrypted"]) != self.encrypted:
            _fail("INVALID_SCHEMA")
        return aid, job

    def _snapshot(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        aid, job = self._controlled_job(command, identity)
        if job["state"] != "OPEN":
            _fail("INVALID_SCHEMA")
        provider = self.deps.get("snapshot")
        if provider is None:
            _fail("NOT_CONFIGURED", state="BLOCKED")
        stage = self._dir(aid)
        if stage.is_symlink():
            _fail("INVALID_PATH")
        stage.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage.chmod(0o700)
        temp = stage / "snapshot.partial"
        temp.unlink(missing_ok=True)
        result = provider.capture(temp, job_id=job["job_id"])
        if not isinstance(result, Mapping) or set(result) != {"sha256", "integrity_ok", "source_generation"}:
            _fail("INVALID_SCHEMA")
        if result["integrity_ok"] is not True or not isinstance(result["source_generation"], str) or not result["source_generation"]:
            _fail("INVALID_SCHEMA")
        integrity_check(temp)
        temp.chmod(0o600)
        sha = file_digest(temp)
        if sha != _hash(result["sha256"]):
            _fail("SOURCE_SHA_MISMATCH")
        with temp.open("rb") as f:
            os.fsync(f.fileno())
        os.replace(temp, stage / "snapshot.sqlite")
        fsync_dir(stage)
        size = (stage / "snapshot.sqlite").stat().st_size
        def apply(tx: Any) -> Outcome:
            tx.execute("UPDATE archive_jobs SET state='SNAPSHOTTED',snapshot_sha256=?,snapshot_size=? WHERE archive_id=? AND state='OPEN'",
                       (sha, size, aid))
            rid = self._event(tx, command, identity, "ArchiveSnapshotVerified", {"archive_id": aid, "snapshot_sha256": sha, "source_generation": result["source_generation"]})
            return Outcome(state="ACCEPTED", code="SNAPSHOTTED", receipt_id=rid,
                           payload={"archive_id": aid, "snapshot_sha256": sha, "snapshot_size": size})
        return self._commit(command, identity, apply)

    def _compress(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        aid, job = self._controlled_job(command, identity)
        if job["state"] != "SNAPSHOTTED":
            _fail("INVALID_SCHEMA")
        stage = self._dir(aid)
        snapshot = stage / "snapshot.sqlite"
        if file_digest(snapshot) != job["snapshot_sha256"] or snapshot.stat().st_size != job["snapshot_size"]:
            _fail("SOURCE_SHA_MISMATCH")
        integrity_check(snapshot)
        refs = json.loads(job["evidence_json"])
        evidence = self.deps.get("evidence")
        if refs and evidence is None:
            _fail("NOT_CONFIGURED", state="BLOCKED")
        records: list[dict[str, Any]] = []
        for ref in refs:
            data = evidence.fetch(kind=ref["kind"], reference=ref["reference"], revision=ref["revision"])
            if not isinstance(data, bytes) or not 0 < len(data) <= _MAX_EVIDENCE_BYTES or digest(data) != ref["sha256"]:
                _fail("SOURCE_SHA_MISMATCH")
            records.append({**ref, "data": data})
        db = stage / "package.sqlite"
        manifest = build_database(snapshot, records, db, archive_id=aid)
        part = stage / "archive.partial"
        part.unlink(missing_ok=True)
        packed_sha = compress_database(db, part)
        check = inspect_archive(part)
        if check["archive_sha256"] != packed_sha or check["manifest"] != manifest:
            _fail("SOURCE_SHA_MISMATCH")
        def apply(tx: Any) -> Outcome:
            tx.execute("UPDATE archive_jobs SET state='COMPRESSED',manifest_sha256=?,archive_sha256=?,record_count=? WHERE archive_id=? AND state='SNAPSHOTTED'",
                       (check["manifest_sha256"], packed_sha, len(records), aid))
            rid = self._event(tx, command, identity, "ArchiveCompressionVerified", {"archive_id": aid, "archive_sha256": packed_sha, "manifest_sha256": check["manifest_sha256"]})
            return Outcome(state="ACCEPTED", code="COMPRESSED", receipt_id=rid,
                           payload={"archive_id": aid, "archive_sha256": packed_sha,
                                    "manifest_sha256": check["manifest_sha256"], "status": "NOT_VERIFIED"})
        return self._commit(command, identity, apply)

    @contextmanager
    def _plaintext(self, aid: str, path: Path) -> Iterator[Path]:
        if not self.encrypted:
            yield path
            return
        cryptor = self.deps.get("cryptor")
        if cryptor is None:
            _fail("NOT_CONFIGURED", state="BLOCKED")
        with tempfile.TemporaryDirectory(prefix="nomad-archive-decrypt-") as temp:
            plain = Path(temp) / "decrypted.sqlite.gz"
            cryptor.decrypt_file(path, plain, archive_id=aid)
            plain.chmod(0o600)
            yield plain

    def _check_final(self, aid: str, job: Mapping[str, Any], path: Path) -> None:
        with self._plaintext(aid, path) as plain:
            check = inspect_archive(plain)
            if (check["archive_sha256"] != job["archive_sha256"]
                    or check["manifest_sha256"] != job["manifest_sha256"]
                    or check["snapshot_sha256"] != job["snapshot_sha256"]
                    or check["record_count"] != job["record_count"]):
                _fail("SOURCE_SHA_MISMATCH")
        if job["stored_sha256"] and file_digest(path) != job["stored_sha256"]:
            _fail("SOURCE_SHA_MISMATCH")

    def _seal(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        aid, job = self._controlled_job(command, identity)
        if job["state"] != "COMPRESSED":
            _fail("INVALID_SCHEMA")
        path = self._final(aid)
        if path.exists():
            self._check_final(aid, job, path)  # recover crash after atomic rename
            stored_sha = file_digest(path)
        else:
            stage = self._dir(aid)
            part = stage / "archive.partial"
            if not part.is_file():
                _fail("INVALID_SCHEMA")
            check = inspect_archive(part)
            if (check["archive_sha256"] != job["archive_sha256"] or
                    check["manifest_sha256"] != job["manifest_sha256"] or
                    check["snapshot_sha256"] != job["snapshot_sha256"] or
                    check["record_count"] != job["record_count"]):
                _fail("SOURCE_SHA_MISMATCH")
            if self.encrypted:
                cryptor = self.deps.get("cryptor")
                if cryptor is None:
                    _fail("NOT_CONFIGURED", state="BLOCKED")
                cipher_part = stage / "encrypted.partial"
                cipher_part.unlink(missing_ok=True)
                cryptor.encrypt_file(part, cipher_part, archive_id=aid)
                cipher_part.chmod(0o600)
                with self._plaintext(aid, cipher_part) as plain:
                    if file_digest(plain) != job["archive_sha256"]:
                        _fail("SOURCE_SHA_MISMATCH")
                candidate = cipher_part
            else:
                candidate = part
            with candidate.open("rb") as f:
                os.fsync(f.fileno())
            stored_sha = file_digest(candidate)
            # One archive root/service owner only. Never overwrite an existing seal.
            try:
                os.link(candidate, path)  # atomically create without replacing
                fsync_dir(self.root)
            except FileExistsError:
                self._check_final(aid, job, path)
                stored_sha = file_digest(path)
        def apply(tx: Any) -> Outcome:
            tx.execute("UPDATE archive_jobs SET state='SEALED_UNWITNESSED',stored_sha256=? WHERE archive_id=? AND state='COMPRESSED'",
                       (stored_sha, aid))
            rid = self._event(tx, command, identity, "ArchiveSealedUnwitnessed", {"archive_id": aid, "stored_sha256": stored_sha})
            return Outcome(state="ACCEPTED", code="SEALED_UNWITNESSED", receipt_id=rid,
                           payload={"archive_id": aid, "stored_sha256": stored_sha,
                                    "archive_sha256": job["archive_sha256"], "status": "NOT_VERIFIED"})
        return self._commit(command, identity, apply)

    def _restore_proof(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        _require_fields(command.payload, {"archive_id", "witness_ref", "archive_sha256", "manifest_sha256", "snapshot_sha256", "record_count", "proof"})
        aid = _identifier(command.payload["archive_id"])
        job = self._job(aid)
        if command.job_id != job["job_id"]:
            _fail("INVALID_SCOPE")
        if job["state"] != "SEALED_UNWITNESSED":
            _fail("INVALID_SCHEMA")
        if identity.principal_id == job["created_by"]:
            _fail("SELF_REVIEW")
        if (command.payload["archive_sha256"] != job["archive_sha256"] or
                command.payload["manifest_sha256"] != job["manifest_sha256"] or
                command.payload["snapshot_sha256"] != job["snapshot_sha256"] or
                command.payload["record_count"] != job["record_count"]):
            _fail("SOURCE_SHA_MISMATCH")
        witness_ref = _string(command.payload["witness_ref"])
        verifier = self.deps.get("witness_verifier")
        if verifier is None:
            _fail("NOT_CONFIGURED", state="BLOCKED")
        path = self._final(aid)
        self._check_final(aid, job, path)
        # The verifier must validate a *separate* trusted issuer and an actual
        # offline restore result; a command payload alone cannot mint a PASS.
        if verifier.verify(command.payload, identity=identity,
                           stored_sha256=job["stored_sha256"], archive_path=path) is not True:
            _fail("IDENTITY_UNVERIFIED")
        def apply(tx: Any) -> Outcome:
            tx.execute("UPDATE archive_jobs SET state='WITNESSED',witness_ref=?,witness_by=? WHERE archive_id=? AND state='SEALED_UNWITNESSED'",
                       (witness_ref, identity.principal_id, aid))
            rid = self._event(tx, command, identity, "ArchiveIndependentlyRestored", {"archive_id": aid, "witness_ref": witness_ref, "witness_by": identity.principal_id})
            return Outcome(state="ACCEPTED", code="WITNESSED", receipt_id=rid,
                           payload={"archive_id": aid, "status": "VERIFIED", "witness_ref": witness_ref})
        return self._commit(command, identity, apply)

    def query(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        try:
            if (not isinstance(query, QueryEnvelope) or query.version != "NOMAD/QUERY/1" or
                    query.domain != "archive" or query.operation != "archive.catalogue"):
                _fail("MALFORMED_REQUEST")
            self._authorize(identity, "archive:read")
            _require_fields(query.payload, set(), {"archive_id", "reference", "kind", "revision"})
            if query.cursor is not None:
                _fail("NOT_SUPPORTED")
            if not isinstance(query.limit, int) or isinstance(query.limit, bool) or not 1 <= query.limit <= 100:
                _fail("INVALID_SCHEMA")
            if "archive_id" not in query.payload:
                if query.payload:
                    _fail("INVALID_SCHEMA")
                rows = self.state_store.read(lambda rd: rd.fetch_all(
                    "SELECT archive_id,job_id,state,manifest_sha256,archive_sha256,stored_sha256,record_count,witness_ref FROM archive_jobs ORDER BY archive_id LIMIT ?",
                    (query.limit,)))
                return Outcome(state="ACCEPTED", code="CATALOGUE", payload={"archives": [dict(row) for row in rows]})
            aid = _identifier(query.payload["archive_id"])
            job = self._job(aid)
            meta = {"archive_id": aid, "state": job["state"], "manifest_sha256": job["manifest_sha256"],
                    "archive_sha256": job["archive_sha256"], "record_count": job["record_count"],
                    "witness_ref": job["witness_ref"], "status": "VERIFIED" if job["state"] == "WITNESSED" else "NOT_VERIFIED"}
            if "reference" in query.payload:
                reference = _string(query.payload["reference"])
                if job["state"] not in {"SEALED_UNWITNESSED", "WITNESSED"}:
                    _fail("INVALID_SCHEMA")
                with self._plaintext(aid, self._final(aid)) as plain:
                    parsed = inspect_archive(plain)
                if parsed["archive_sha256"] != job["archive_sha256"]:
                    _fail("SOURCE_SHA_MISMATCH")
                matches = [entry for entry in parsed["manifest"]["entries"] if
                           entry["reference"] == reference and
                           ("kind" not in query.payload or entry["kind"] == _string(query.payload["kind"])) and
                           ("revision" not in query.payload or entry["revision"] == _string(query.payload["revision"]))]
                meta["provenance"] = matches[:query.limit]
            elif len(query.payload) > 1:
                _fail("INVALID_SCHEMA")
            return Outcome(state="ACCEPTED", code="CATALOGUE", payload=meta)
        except CommandFailure as e:
            return Outcome(state=e.state, code=e.code)
        except (ArchiveError, ValueError, KeyError, TypeError, OSError) as e:
            return Outcome(state="DENIED", code="INVALID_SCHEMA", payload={"reason": type(e).__name__})


def build_component(deps: Mapping[str, object]) -> ArchiveComponent:
    """Public frozen facade factory, returned object implements Component."""
    return ArchiveComponent(deps)
