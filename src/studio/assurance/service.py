"""Assurance domain commands. SQL transactions/idempotency are owned by WG-C.

The coordinator passes verified ActorContext from WG-J, a WG-C scoped transaction
executor, WG-G original-blob/evidence readback, and capability validators. This
module opens no database and has no model-inference or external-effect path.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence, TypeVar


class AssuranceError(Exception):
    """Stable OpenAPI error.code with non-sensitive public message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReviewerAssignment:
    principal_id: str
    reviewer_root_worker_id: str | None


@dataclass(frozen=True)
class ValidationResult:
    status: Literal["pass", "fail", "incomplete"]
    transcript: bytes


class ScopedTransaction(Protocol):
    def one(self, sql: str, params: Sequence[Any] = ()) -> Mapping[str, Any] | None: ...
    def all(self, sql: str, params: Sequence[Any] = ()) -> list[Mapping[str, Any]]: ...
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...
    def event(self, *, event_type: str, subject_id: str, payload_digest: str) -> None: ...


T = TypeVar("T")


class ScopedStore(Protocol):
    """Implemented by WG-C: project-scoped serializable transaction + idempotency.

    run() atomically binds (project, 'E', operation, principal, request_id,
    request_digest) to the returned response; changed-body replay conflicts.
    The callback is invoked exactly once for a new key, and events are committed
    in the same transaction. Read-only operations use read(). WG-C restricts
    mutating SQL to E-owned tables.
    """

    def run(self, *, project_id: str, domain: str, operation: str,
            principal_id: str, request_id: str, request_digest: str,
            command: Callable[[ScopedTransaction], T]) -> T: ...
    def read(self, *, project_id: str, query: Callable[[ScopedTransaction], T]) -> T: ...


class OriginalBytes(Protocol):
    """WG-G trusted content address readback (no user-supplied bytes)."""

    def get(self, *, project_id: str, digest: str) -> bytes: ...
    def put_verified(self, *, project_id: str, content: bytes) -> str: ...


class ReviewerDirectory(Protocol):
    """Independent reviewer selection under WG-A authorized project policy."""

    def choose(self, *, project_id: str, submission_id: str,
               author_shift_id: str) -> ReviewerAssignment: ...


class CapabilityValidators(Protocol):
    """Real installed capability validator, mediated by the authorized tool host.

    Return None only when the declared validator cannot run in this environment.
    Validators must include a transcript of the test actually executed, whether
    successful or failed. A validator is not authored or selected by a worker.
    """

    def run(self, *, project_id: str, kind: str, validator_digest: str,
            acceptance_digest: str, original: bytes) -> ValidationResult | None: ...


SHA_LEN = 64


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssuranceError("invalid", f"{name} must be a nonempty string")
    return value


def _sha(value: Any, name: str) -> str:
    value = _required(value, name)
    if len(value) != SHA_LEN or any(c not in "0123456789abcdef" for c in value):
        raise AssuranceError("invalid", f"{name} must be a lowercase SHA-256 digest")
    return value


def _verify_bytes(blob: bytes, digest: str, label: str) -> None:
    if not isinstance(blob, bytes) or _digest(blob) != digest:
        raise AssuranceError("conflict", f"{label} failed exact-byte digest readback")


def _assert_fields(data: Mapping[str, Any], required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(data, Mapping) or set(data) - required - optional or required - set(data):
        raise AssuranceError("invalid", "request field shape differs from frozen contract")


class AssuranceService:
    def __init__(self, store: ScopedStore, original: OriginalBytes,
                 reviewers: ReviewerDirectory, validators: CapabilityValidators):
        self.store, self.original, self.reviewers, self.validators = store, original, reviewers, validators

    @staticmethod
    def _authorized(tx: ScopedTransaction, actor: Mapping[str, Any], project_id: str) -> str:
        # J must authenticate ActorContext before calling this service. Never take
        # principal identity from an HTTP request body or relay payload.
        if not isinstance(actor, Mapping) or project_id not in actor.get("allowed_project_ids", ()):  # J trusted context
            raise AssuranceError("forbidden", "project not authorized")
        principal = _required(actor.get("principal_id"), "actor.principal_id")
        if not _required(actor.get("issuer"), "actor.issuer") or not _required(actor.get("trusted_proof_ref"), "actor.trusted_proof_ref"):
            raise AssuranceError("forbidden", "trusted actor context required")
        if not tx.one("SELECT 1 FROM project_grants WHERE project_id=? AND principal_id=? LIMIT 1", (project_id, principal)):
            raise AssuranceError("forbidden", "project grant absent")
        return principal

    @staticmethod
    def _independent(tx: ScopedTransaction, project_id: str, author: str,
                     reviewer: str, reviewer_root: str | None) -> dict[str, Any]:
        author_row = tx.one("""
            SELECT w.principal_id AS author_principal_id,sh.worker_id
            FROM worker_shifts sh JOIN worker_conversations w
              ON w.project_id=sh.project_id AND w.worker_id=sh.worker_id
            WHERE sh.project_id=? AND sh.shift_id=?""", (project_id,author))
        principal = tx.one("SELECT kind FROM principals WHERE principal_id=?", (reviewer,))
        if author_row is None or principal is None or author_row["author_principal_id"] == reviewer:
            raise AssuranceError("forbidden", "reviewer must be a distinct trusted principal")
        if not tx.one("SELECT 1 FROM project_grants WHERE project_id=? AND principal_id=? LIMIT 1",
                      (project_id,reviewer)):
            raise AssuranceError("forbidden", "reviewer lacks a project grant")
        author_lineage = tx.one("SELECT root_worker_id FROM worker_lineages "
                                "WHERE project_id=? AND worker_id=?",
                                (project_id,author_row["worker_id"]))
        if author_lineage is None:
            raise AssuranceError("conflict", "author persistent conversation lineage unavailable")
        if reviewer_root is None:
            if principal["kind"] != "human":
                raise AssuranceError("forbidden", "nonhuman reviewer requires independently verified lineage")
        else:
            root = tx.one("SELECT root_worker_id FROM worker_lineages "
                          "WHERE project_id=? AND worker_id=?", (project_id,reviewer_root))
            participant = tx.one("""
                SELECT 1 AS ok FROM worker_lineages l JOIN worker_conversations w
                  ON w.project_id=l.project_id AND w.worker_id=l.worker_id
                WHERE l.project_id=? AND l.root_worker_id=? AND w.principal_id=? LIMIT 1""",
                (project_id,reviewer_root,reviewer))
            if (root is None or root["root_worker_id"] != reviewer_root
                    or reviewer_root == author_lineage["root_worker_id"] or participant is None):
                raise AssuranceError("forbidden", "reviewer lineage is missing, shared, or untrusted")
        return {"author_shift_id":author,
                "author_root_worker_id":author_lineage["root_worker_id"],
                "reviewer_principal_id":reviewer,
                "reviewer_root_worker_id":reviewer_root}

    @staticmethod
    def _submission(tx: ScopedTransaction, project: str, submission_id: str,
                    acceptance_digest: str | None = None) -> Mapping[str, Any]:
        row = tx.one("""
            SELECT s.submission_id,s.shift_id,s.task_id,s.lease_generation,s.state,
                   s.source_revision_id,s.manifest_digest,
                   t.acceptance_digest,t.snapshot_id,
                   t.snapshot_id AS author_snapshot
            FROM submissions s
            JOIN tasks t ON t.project_id=s.project_id AND t.task_id=s.task_id
            JOIN shift_assignments a ON a.project_id=s.project_id
              AND a.shift_id=s.shift_id AND a.task_id=s.task_id
              AND a.lease_generation=s.lease_generation
            WHERE s.project_id=? AND s.submission_id=?""", (project,submission_id))
        if row is None:
            raise AssuranceError("missing", "submission unavailable or unbound to worker shift")
        if row["state"] not in ("submitted","qa_passed","integrated"):
            raise AssuranceError("conflict", "submission has not reached submitted state")
        if acceptance_digest is not None and row["acceptance_digest"] != acceptance_digest:
            raise AssuranceError("conflict", "task acceptance digest differs")
        return row

    def _bound_artifacts(self, tx: ScopedTransaction, project_id: str,
                         s: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = tx.all("""SELECT a.artifact_id,a.kind,a.content_digest,a.manifest_digest,
                     a.producer_shift_id,a.source_revision_id,a.snapshot_id,
                     b.durability
                     FROM submission_artifacts sa JOIN artifacts a
                       ON a.project_id=sa.project_id AND a.artifact_id=sa.artifact_id
                     JOIN blobs b ON b.content_digest=a.content_digest
                     WHERE sa.project_id=? AND sa.submission_id=? ORDER BY a.artifact_id""",
                      (project_id, s["submission_id"]))
        if not rows:
            raise AssuranceError("missing", "no submitted original artifacts")
        result = []
        for row in rows:
            a = dict(row)
            if (a["durability"] != "verified" or a["source_revision_id"] != s["source_revision_id"]
                    or a["snapshot_id"] != s["snapshot_id"] or a["producer_shift_id"] != s["shift_id"]):
                raise AssuranceError("conflict", "original artifact is not verified against author/source/Canon")
            for digest in (a["content_digest"], a["manifest_digest"]):
                _sha(digest, "artifact digest")
                if not tx.one("SELECT 1 FROM project_blobs pb JOIN blobs b ON b.content_digest=pb.content_digest WHERE pb.project_id=? AND pb.content_digest=? AND b.durability='verified'", (project_id, digest)):
                    raise AssuranceError("missing", "artifact original or manifest has no verified project-scoped blob")
            result.append({k: a[k] for k in ("artifact_id", "kind", "content_digest", "manifest_digest", "producer_shift_id", "source_revision_id", "snapshot_id")})
        return result

    def _binding(self, tx: ScopedTransaction, project_id: str, s: Mapping[str, Any]) -> dict[str, Any]:
        source = tx.one("SELECT repo_id,commit_sha,tree_digest FROM source_revisions WHERE project_id=? AND revision_id=?", (project_id, s["source_revision_id"])) if s["source_revision_id"] else None
        if not source:
            raise AssuranceError("missing", "submitted source revision unavailable")
        return {"project_id": project_id, "submission_id": s["submission_id"],
                "source_commit_sha": source["commit_sha"], "source_tree_digest": source["tree_digest"],
                "source_repo_id": source["repo_id"], "author_shift_id": s["shift_id"], "lease_generation": s["lease_generation"],
                "submission_manifest_digest": s["manifest_digest"],
                "source_revision_id": s["source_revision_id"], "acceptance_digest": s["acceptance_digest"],
                "canon_snapshot_id": s["snapshot_id"], "artifacts": self._bound_artifacts(tx, project_id, s)}

    def _evidence(self, project_id: str, data: bytes) -> str:
        digest = self.original.put_verified(project_id=project_id, content=data)
        _sha(digest, "evidence digest")
        if digest != _digest(data):
            raise AssuranceError("conflict", "evidence storage returned incorrect digest")
        _verify_bytes(self.original.get(project_id=project_id, digest=digest), digest, "evidence")
        return digest

    def requestReview(self, actor: Mapping[str, Any], project_id: str,
                      request: Mapping[str, Any]) -> dict[str, str]:
        _assert_fields(request, {"request_id", "submission_id", "acceptance_digest"})
        request_id = _required(request["request_id"], "request_id")
        submission = _required(request["submission_id"], "submission_id")
        acceptance = _sha(request["acceptance_digest"], "acceptance_digest")

        def command(tx: ScopedTransaction) -> dict[str, str]:
            self._authorized(tx, actor, project_id)
            s = self._submission(tx, project_id, submission, acceptance)
            if s["state"] != "submitted":
                raise AssuranceError("conflict", "new review requires a submitted candidate")
            binding = self._binding(tx, project_id, s)
            assignee = self.reviewers.choose(project_id=project_id, submission_id=submission, author_shift_id=s["shift_id"])
            independence = self._independent(tx, project_id, s["shift_id"], assignee.principal_id, assignee.reviewer_root_worker_id)
            # Persist the exact original submission AND reviewer assignment. The
            # selected root cannot be silently substituted during later review.
            bound_digest = self._evidence(project_id, _canonical({"binding": binding,
                "reviewer_principal_id": assignee.principal_id,
                "reviewer_root_worker_id": assignee.reviewer_root_worker_id,
                "independence": independence}))
            review_id = uuid.uuid4().hex
            tx.execute("""INSERT INTO qa_reviews(project_id,review_id,submission_id,reviewer_principal_id,
                         author_shift_id,source_revision_id,acceptance_digest,verdict,evidence_digest)
                         VALUES(?,?,?,?,?,?,?,'pending',?)""",
                       (project_id, review_id, submission, assignee.principal_id, s["shift_id"], s["source_revision_id"], acceptance, bound_digest))
            tx.event(event_type="qa.review.requested", subject_id=review_id, payload_digest=bound_digest)
            return {"review_id": review_id, "state": "pending"}
        return self.store.run(project_id=project_id, domain="E", operation="requestReview",
                              principal_id=_required(actor.get("principal_id"), "principal_id"),
                              request_id=request_id, request_digest=_digest(_canonical(request)), command=command)

    def _actual_validation(self, project: str, binding: Mapping[str, Any]) -> tuple[str, list[dict[str, str]]]:
        receipts: list[dict[str, str]] = []
        states: list[str] = []
        for artifact in binding["artifacts"]:
            original_digest = artifact["content_digest"]
            content = self.original.get(project_id=project, digest=original_digest)
            _verify_bytes(content, original_digest, "original artifact")
            m_digest = artifact["manifest_digest"]
            manifest_bytes = self.original.get(project_id=project, digest=m_digest)
            _verify_bytes(manifest_bytes, m_digest, "artifact manifest")
            try:
                manifest = json.loads(manifest_bytes)
            except (UnicodeDecodeError, ValueError) as exc:
                raise AssuranceError("invalid", "original artifact manifest is not JSON") from exc
            expected = {"project_id": project, "artifact_id": artifact["artifact_id"],
                        "kind": artifact["kind"], "content_digest": original_digest,
                        "producer_shift_id": artifact["producer_shift_id"],
                        "source_revision_id": artifact["source_revision_id"], "canon_snapshot_id": artifact["snapshot_id"]}
            # The frozen manifest schema contains a self-referential manifest_digest.
            # Never require an impossible SHA-256 fixed point; the DB digest is
            # authenticated against original bytes independently above.
            if not isinstance(manifest, dict) or not isinstance(manifest.get("manifest_digest"), str) or not manifest["manifest_digest"] or any(manifest.get(k) != v for k, v in expected.items()):
                raise AssuranceError("conflict", "artifact manifest fails exact submission binding")
            validator_digest = manifest.get("validator_digest")
            if validator_digest is None:
                result = None
            else:
                _sha(validator_digest, "validator_digest")
                result = self.validators.run(project_id=project, kind=artifact["kind"],
                                             validator_digest=validator_digest,
                                             acceptance_digest=binding["acceptance_digest"], original=content)
            if result is None:
                state, transcript = "incomplete", b"validator unavailable or unspecified"
            else:
                if result.status not in ("pass", "fail", "incomplete") or not isinstance(result.transcript, bytes) or not result.transcript:
                    raise AssuranceError("invalid", "trusted validator returned invalid result")
                state, transcript = result.status, result.transcript
            # Actual execution output (or explicit absence) persisted and read back by G.
            transcript_digest = self._evidence(project, transcript)
            receipt = {"artifact_id": artifact["artifact_id"], "original_digest": original_digest,
                       "manifest_digest": m_digest, "source_revision_id": artifact["source_revision_id"],
                       "acceptance_digest": binding["acceptance_digest"],
                       "validator_digest": validator_digest or _digest(b"unavailable-validator"),
                       "transcript_digest": transcript_digest, "status": state}
            receipt["evidence_digest"] = self._evidence(project, _canonical(receipt))
            receipts.append(receipt)
            states.append(state)
        verdict = "fail" if "fail" in states else "incomplete" if "incomplete" in states else "pass"
        return verdict, receipts

    def submitReview(self, actor: Mapping[str, Any], project_id: str, review_id: str,
                     request: Mapping[str, Any]) -> dict[str, str]:
        _assert_fields(request, {"request_id", "verdict", "evidence_digests"}, {"source_revision_id"})
        request_id = _required(request["request_id"], "request_id")
        _required(review_id, "review_id")
        if request["verdict"] not in ("pass", "fail", "incomplete"):
            raise AssuranceError("invalid", "verdict must be pass, fail or incomplete")
        if not isinstance(request["evidence_digests"], list) or len(set(request["evidence_digests"])) != len(request["evidence_digests"]):
            raise AssuranceError("invalid", "evidence_digests must be a unique array")
        for digest in request["evidence_digests"]:
            _sha(digest, "evidence digest")
        if "source_revision_id" in request:
            _required(request["source_revision_id"], "source_revision_id")

        def command(tx: ScopedTransaction) -> dict[str, str]:
            principal = self._authorized(tx, actor, project_id)
            q = tx.one("SELECT * FROM qa_reviews WHERE project_id=? AND review_id=?", (project_id, review_id))
            if q is None:
                raise AssuranceError("missing", "review unavailable in this project")
            if q["verdict"] != "pending":
                raise AssuranceError("conflict", "review is immutable after verdict; create a new review")
            if q["reviewer_principal_id"] != principal:
                raise AssuranceError("forbidden", "caller is not independently assigned reviewer")
            if "source_revision_id" in request and request["source_revision_id"] != q["source_revision_id"]:
                raise AssuranceError("stale_revision", "review source revision mismatch")
            s = self._submission(tx, project_id, q["submission_id"], q["acceptance_digest"])
            if s["source_revision_id"] != q["source_revision_id"] or s["shift_id"] != q["author_shift_id"]:
                raise AssuranceError("stale_revision", "submission/source identity changed")
            expected = self._binding(tx, project_id, s)
            bound_digest = q["evidence_digest"]
            if not bound_digest:
                raise AssuranceError("conflict", "review binding evidence missing")
            bound_raw = self.original.get(project_id=project_id, digest=bound_digest)
            _verify_bytes(bound_raw, bound_digest, "review binding")
            try:
                bound = json.loads(bound_raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise AssuranceError("conflict", "review binding was not valid JSON") from exc
            if not isinstance(bound, dict) or bound.get("binding") != expected or bound.get("reviewer_principal_id") != principal:
                raise AssuranceError("conflict", "submitted artifact set, source, or assigned reviewer changed")
            reviewer_root = bound.get("reviewer_root_worker_id")
            if reviewer_root is not None and not isinstance(reviewer_root, str):
                raise AssuranceError("conflict", "invalid pinned reviewer root")
            proof = self._independent(tx, project_id, q["author_shift_id"], principal, reviewer_root)
            if bound.get("independence") != proof:
                raise AssuranceError("conflict", "reviewer provenance changed since request")
            verdict, receipts = self._actual_validation(project_id, expected)
            # API evidence is not authority: if supplied, it must be precisely the
            # receipts produced by actual original-byte validation in this command.
            receipt_digests = {r["evidence_digest"] for r in receipts}
            supplied = set(request["evidence_digests"])
            # Manual reviewer findings require a real original evidence record,
            # explicitly bound to this reviewer, candidate and requested verdict.
            # They can lower a validator PASS, never elevate a failed check to PASS.
            finding_digests: list[str] = []
            for claimed in sorted(supplied - receipt_digests):
                data = self.original.get(project_id=project_id, digest=claimed)
                _verify_bytes(data, claimed, "independent reviewer finding")
                try:
                    finding = json.loads(data)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise AssuranceError("invalid", "review finding must be JSON") from exc
                needed = {"kind": "review_finding", "project_id": project_id, "review_id": review_id,
                          "source_revision_id": s["source_revision_id"], "reviewer_principal_id": principal,
                          "binding_digest": bound_digest, "verdict": request["verdict"]}
                if (not isinstance(finding, dict) or any(finding.get(k) != v for k, v in needed.items())
                        or not isinstance(finding.get("reason"), str) or not finding["reason"].strip()
                        or finding["verdict"] not in ("fail", "incomplete")):
                    raise AssuranceError("conflict", "reviewer finding does not bind exact verified candidate")
                finding_digests.append(claimed)
            if supplied & receipt_digests and supplied & receipt_digests != receipt_digests:
                raise AssuranceError("conflict", "partial claimed validator receipt set")
            if request["verdict"] in ("fail", "incomplete") and finding_digests:
                # A reviewer may reject artifacts that pass installed validators.
                if request["verdict"] == "fail" or verdict == "pass":
                    verdict = request["verdict"]
            elif verdict == "pass" and request["verdict"] != "pass":
                raise AssuranceError("conflict", "manual negative verdict requires independently read-back finding")
            proof_digest = self._evidence(project_id, _canonical(proof))
            tx.execute("""INSERT INTO qa_independence_proofs(project_id,review_id,author_shift_id,
                         reviewer_principal_id,reviewer_root_worker_id,independence_evidence_digest)
                         VALUES(?,?,?,?,?,?)""", (project_id, review_id, q["author_shift_id"], principal,
                                                 reviewer_root, proof_digest))
            for receipt in receipts:
                tx.execute("""INSERT INTO qa_receipts(project_id,review_id,validator_digest,artifact_id,evidence_digest)
                            VALUES(?,?,?,?,?)""", (project_id, review_id, receipt["validator_digest"],
                                                      receipt["artifact_id"], receipt["evidence_digest"]))
            final_digest = self._evidence(project_id, _canonical({"review_id": review_id, "binding_digest": bound_digest,
                                                                  "independence_proof_digest": proof_digest,
                                                                  "verdict": verdict, "receipts": receipts,
                                                                  "reviewer_findings": finding_digests}))
            tx.execute("UPDATE qa_reviews SET verdict=?,evidence_digest=? WHERE project_id=? AND review_id=? AND verdict='pending'",
                       (verdict, final_digest, project_id, review_id))
            tx.event(event_type="qa.review.verdict", subject_id=review_id, payload_digest=final_digest)
            return {"review_id": review_id, "verdict": verdict}
        return self.store.run(project_id=project_id, domain="E", operation="submitReview",
                              principal_id=_required(actor.get("principal_id"), "principal_id"),
                              request_id=request_id, request_digest=_digest(_canonical({"review_id": review_id, **request})), command=command)

    def listReviews(self, actor: Mapping[str, Any], project_id: str) -> dict[str, Any]:
        def query(tx: ScopedTransaction) -> dict[str, Any]:
            self._authorized(tx, actor, project_id)
            rows = tx.all("SELECT review_id,submission_id,verdict FROM qa_reviews WHERE project_id=? ORDER BY review_id", (project_id,))
            return {"reviews": [dict(row) for row in rows]}
        return self.store.read(project_id=project_id, query=query)

    def openOversightCase(self, actor: Mapping[str, Any], project_id: str,
                          request: Mapping[str, Any]) -> dict[str, str]:
        _assert_fields(request, {"request_id", "subject_id", "evidence_digest"})
        request_id = _required(request["request_id"], "request_id")
        subject = _required(request["subject_id"], "subject_id")
        digest = _sha(request["evidence_digest"], "evidence_digest")

        def command(tx: ScopedTransaction) -> dict[str, str]:
            self._authorized(tx, actor, project_id)
            # Subject IDs are untyped in frozen OpenAPI: resolve existence through
            # known project-scoped domain records, never through claimed relay text.
            subjects = (("submissions", "submission_id"), ("qa_reviews", "review_id"),
                        ("tasks", "task_id"), ("artifacts", "artifact_id"),
                        ("features", "feature_id"), ("worker_shifts", "shift_id"),
                        ("source_revisions", "revision_id"), ("releases", "release_id"),
                        ("owner_decisions", "decision_id"), ("deliberations", "deliberation_id"),
                        ("relay_messages", "message_id"), ("tool_operations", "operation_id"))
            if not any(tx.one(f"SELECT 1 FROM {table} WHERE project_id=? AND {column}=? LIMIT 1", (project_id, subject))
                       for table, column in subjects):
                raise AssuranceError("missing", "oversight subject unavailable in this project")
            _verify_bytes(self.original.get(project_id=project_id, digest=digest), digest, "oversight evidence")
            case_id = uuid.uuid4().hex
            tx.execute("INSERT INTO oversight_cases(project_id,case_id,subject_id,state,evidence_digest) VALUES(?,?,?,'open',?)",
                       (project_id, case_id, subject, digest))
            tx.event(event_type="oversight.opened", subject_id=case_id, payload_digest=digest)
            return {"case_id": case_id, "state": "open"}
        return self.store.run(project_id=project_id, domain="E", operation="openOversightCase",
                              principal_id=_required(actor.get("principal_id"), "principal_id"),
                              request_id=request_id, request_digest=_digest(_canonical(request)), command=command)

    def beginInvestigation(self, actor: Mapping[str, Any], project_id: str, case_id: str,
                           request_id: str) -> dict[str, str]:
        """Internal E command for the frozen open -> investigating transition."""
        _required(request_id, "request_id")
        _required(case_id, "case_id")
        def command(tx: ScopedTransaction) -> dict[str, str]:
            self._authorized(tx, actor, project_id)
            row = tx.one("SELECT state,evidence_digest FROM oversight_cases WHERE project_id=? AND case_id=?", (project_id, case_id))
            if row is None:
                raise AssuranceError("missing", "oversight case unavailable in this project")
            if row["state"] != "open":
                raise AssuranceError("conflict", "investigation can only begin from open state")
            _verify_bytes(self.original.get(project_id=project_id, digest=row["evidence_digest"]), row["evidence_digest"], "case evidence")
            tx.execute("UPDATE oversight_cases SET state='investigating' WHERE project_id=? AND case_id=? AND state='open'", (project_id, case_id))
            tx.event(event_type="oversight.investigating", subject_id=case_id, payload_digest=row["evidence_digest"])
            return {"case_id": case_id, "state": "investigating"}
        return self.store.run(project_id=project_id, domain="E", operation="beginInvestigation",
                              principal_id=_required(actor.get("principal_id"), "principal_id"),
                              request_id=request_id, request_digest=_digest(_canonical({"case_id": case_id})), command=command)

    def escalateReview(self, actor: Mapping[str, Any], project_id: str, review_id: str,
                       request_id: str) -> dict[str, str]:
        """Internal review-to-oversight escalation; uses the public case command."""
        def query(tx: ScopedTransaction) -> str:
            self._authorized(tx, actor, project_id)
            row = tx.one("SELECT verdict,evidence_digest FROM qa_reviews WHERE project_id=? AND review_id=?", (project_id, review_id))
            if row is None:
                raise AssuranceError("missing", "review unavailable")
            if row["verdict"] not in ("fail", "incomplete") or not row["evidence_digest"]:
                raise AssuranceError("conflict", "only failed/incomplete reviews can be escalated")
            return row["evidence_digest"]
        digest = self.store.read(project_id=project_id, query=query)
        return self.openOversightCase(actor, project_id, {"request_id": request_id, "subject_id": review_id,
                                                        "evidence_digest": digest})

    def resolveOversightCase(self, actor: Mapping[str, Any], project_id: str, case_id: str,
                             request: Mapping[str, Any]) -> dict[str, str]:
        _assert_fields(request, {"request_id", "resolution_digest"})
        request_id = _required(request["request_id"], "request_id")
        digest = _sha(request["resolution_digest"], "resolution_digest")
        _required(case_id, "case_id")

        def command(tx: ScopedTransaction) -> dict[str, str]:
            principal = self._authorized(tx, actor, project_id)
            c = tx.one("SELECT subject_id,state,evidence_digest FROM oversight_cases WHERE project_id=? AND case_id=?", (project_id, case_id))
            if c is None:
                raise AssuranceError("missing", "oversight case unavailable in this project")
            if c["state"] not in ("open", "investigating"):
                raise AssuranceError("conflict", "resolved case is immutable")
            raw = self.original.get(project_id=project_id, digest=digest)
            _verify_bytes(raw, digest, "oversight resolution")
            try:
                resolution = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise AssuranceError("invalid", "resolution evidence must be JSON") from exc
            # The single SQL evidence_digest column holds the final resolution
            # envelope; that envelope retains original evidence + decision identity.
            required = {"project_id": project_id, "case_id": case_id, "subject_id": c["subject_id"],
                        "initial_evidence_digest": c["evidence_digest"], "auditor_principal_id": principal}
            if not isinstance(resolution, dict) or any(resolution.get(k) != v for k, v in required.items()) or not isinstance(resolution.get("outcome"), str) or not resolution["outcome"].strip():
                raise AssuranceError("conflict", "resolution evidence is not bound to original case and auditor")
            tx.execute("UPDATE oversight_cases SET state='resolved',evidence_digest=? WHERE project_id=? AND case_id=? AND state IN ('open','investigating')",
                       (digest, project_id, case_id))
            tx.event(event_type="oversight.resolved", subject_id=case_id, payload_digest=digest)
            return {"case_id": case_id, "state": "resolved"}
        return self.store.run(project_id=project_id, domain="E", operation="resolveOversightCase",
                              principal_id=_required(actor.get("principal_id"), "principal_id"),
                              request_id=request_id, request_digest=_digest(_canonical({"case_id": case_id, **request})), command=command)

    def listOversightCases(self, actor: Mapping[str, Any], project_id: str) -> dict[str, Any]:
        def query(tx: ScopedTransaction) -> dict[str, Any]:
            self._authorized(tx, actor, project_id)
            rows = tx.all("SELECT case_id,subject_id,state FROM oversight_cases WHERE project_id=? ORDER BY case_id", (project_id,))
            return {"cases": [dict(row) for row in rows]}
        return self.store.read(project_id=project_id, query=query)

    def requireExactIndependentPass(self, actor: Mapping[str, Any], project_id: str,
                                    submission_id: str, candidate_revision_id: str) -> str:
        """WG-M/QA gate: accepted QA cannot be reused for transformed source bytes.

        Returns the committed QA evidence digest, or rejects a stale/incomplete/
        unverified candidate. No promotion or source state change is performed.
        """
        def query(tx: ScopedTransaction) -> str:
            self._authorized(tx, actor, project_id)
            s = self._submission(tx, project_id, submission_id)
            if not s["source_revision_id"] or s["source_revision_id"] != candidate_revision_id:
                raise AssuranceError("stale_revision", "canonical candidate differs from QA-reviewed source")
            binding = self._binding(tx, project_id, s)
            rows = tx.all("""SELECT q.review_id,q.evidence_digest,q.reviewer_principal_id,
                                    p.reviewer_root_worker_id,p.independence_evidence_digest FROM qa_reviews q
                             JOIN qa_independence_proofs p ON p.project_id=q.project_id AND p.review_id=q.review_id
                             WHERE q.project_id=? AND q.submission_id=? AND q.verdict='pass'
                             AND q.source_revision_id=? AND q.acceptance_digest=?
                             AND p.author_shift_id=q.author_shift_id
                             AND p.reviewer_principal_id=q.reviewer_principal_id ORDER BY q.review_id""",
                          (project_id, submission_id, candidate_revision_id, s["acceptance_digest"]))
            required_artifacts = {a["artifact_id"] for a in binding["artifacts"]}
            for row in rows:
                digest = row["evidence_digest"]
                if not digest:
                    continue
                receipt_rows = tx.all("SELECT artifact_id,evidence_digest FROM qa_receipts WHERE project_id=? AND review_id=?", (project_id, row["review_id"]))
                if {r["artifact_id"] for r in receipt_rows} != required_artifacts:
                    continue
                try:
                    payload = self.original.get(project_id=project_id, digest=digest)
                    _verify_bytes(payload, digest, "QA verdict")
                    evidence = json.loads(payload)
                    review_binding = evidence["binding_digest"]
                    bound = self.original.get(project_id=project_id, digest=review_binding)
                    _verify_bytes(bound, review_binding, "QA original binding")
                    pinned = json.loads(bound)
                    if (pinned.get("binding") != binding
                            or pinned.get("reviewer_principal_id") != row["reviewer_principal_id"]
                            or pinned.get("reviewer_root_worker_id") != row["reviewer_root_worker_id"]):
                        continue
                    if evidence["independence_proof_digest"] != row["independence_evidence_digest"]:
                        continue
                    _verify_bytes(self.original.get(project_id=project_id, digest=row["independence_evidence_digest"]),
                                  row["independence_evidence_digest"], "independent reviewer proof")
                    if evidence["verdict"] != "pass" or {r["evidence_digest"] for r in evidence["receipts"]} != {r["evidence_digest"] for r in receipt_rows}:
                        continue
                    if any(r["status"] != "pass" for r in evidence["receipts"]):
                        continue
                    if evidence.get("reviewer_findings"):
                        continue
                    for a in binding["artifacts"]:
                        _verify_bytes(self.original.get(project_id=project_id, digest=a["content_digest"]), a["content_digest"], "QA original artifact")
                        _verify_bytes(self.original.get(project_id=project_id, digest=a["manifest_digest"]), a["manifest_digest"], "QA original manifest")
                    for r in evidence["receipts"]:
                        rec = self.original.get(project_id=project_id, digest=r["evidence_digest"])
                        _verify_bytes(rec, r["evidence_digest"], "QA original receipt")
                        transcript = self.original.get(project_id=project_id, digest=r["transcript_digest"])
                        _verify_bytes(transcript, r["transcript_digest"], "QA validator transcript")
                    return digest
                except (KeyError, ValueError, TypeError, AssuranceError):
                    continue
            raise AssuranceError("qa_failed", "no independent PASS bound to this exact candidate")
        return self.store.read(project_id=project_id, query=query)
