"""Fail-closed V1.6 admission and publication reservation.

This component never pushes/merges. Publishing requires WG-C atomic reserve,
WG-G isolated writer + provider readback, and separately approved cutover.
"""
from __future__ import annotations

import hashlib
import secrets
import re
from datetime import datetime, timezone
from typing import Callable

from .contracts import ContractError, canonical_hash, validate
from .interfaces import AuditSink, Authority, DriveSource, GitSource, Principal, State
from .paths import validate_paths


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcstr(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AdmissionError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Gateway:
    def __init__(self, *, authority: Authority | None = None, git: GitSource | None = None,
                 drive: DriveSource | None = None, audit: AuditSink | None = None,
                 clock: Callable[[], datetime] = utcnow):
        self.authority, self.git, self.drive, self.audit, self.clock = (
            authority, git, drive, audit, clock)

    def _check_state(self, request: dict, principal: Principal | None, state: State,
                     operation: str) -> None:
        if principal is None or principal.auth_method != "MTLS":
            raise AdmissionError("IDENTITY_UNVERIFIED")
        if request.get("claimed_principal_id", request.get("principal_id")) != principal.principal_id:
            raise AdmissionError("IDENTITY_UNVERIFIED")
        if not state.grant_issuer_verified:
            raise AdmissionError("ACCESS_REQUIRED")
        try:
            grant = validate(dict(state.grant), "AUTH-V16/GRANT/1")
        except ContractError as e:
            raise AdmissionError(e.code) from None
        if request["grant_id"] != grant["grant_id"]:
            raise AdmissionError("ACCESS_REQUIRED")
        if not state.owner_approved or request.get("owner_decision_ref", grant["owner_decision_ref"]) != grant["owner_decision_ref"]:
            raise AdmissionError("OWNER_APPROVAL_REQUIRED")
        if (grant["principal_id"] != principal.principal_id or
            any(request[k] != grant[k] for k in ("job_id", "worker_id")) or
            request.get("repo_id", grant["repo_id"]) != grant["repo_id"] or
            state.approved_scope_sha256 != grant["approved_scope_sha256"] or
            request.get("approved_scope_sha256", grant["approved_scope_sha256"]) != grant["approved_scope_sha256"]):
            raise AdmissionError("INVALID_SCOPE")
        if (request["policy_revision"] != state.policy_revision or
            grant["policy_revision"] != state.policy_revision):
            raise AdmissionError("POLICY_REVISION_MISMATCH")
        if (request["authority_revision"] != state.authority_revision or
            grant["authority_revision"] != state.authority_revision):
            raise AdmissionError("AUTHORITY_REVISION_MISMATCH")
        if state.revoked or grant["revoked_at"] is not None:
            raise AdmissionError("REVOKED_GRANT")
        now = self.clock()
        if now < parse_utc(grant["issued_at"]) or now >= parse_utc(grant["expires_at"]):
            raise AdmissionError("EXPIRED_GRANT")
        if request["assignment_epoch"] != state.assignment_epoch or grant["assignment_epoch"] != state.assignment_epoch:
            raise AdmissionError("STALE_ASSIGNMENT")
        if (request["lease_generation"] != state.lease_generation or
            grant["lease_generation"] != state.lease_generation or
            request["attempt_id"] != state.holder_attempt_id):
            raise AdmissionError("STALE_LEASE")
        if operation not in grant["allowed_operations"]:
            raise AdmissionError("INVALID_SCOPE")
        if operation not in {"SOURCE_BRANCH_UPDATE", "DRIVE_REVISION_UPDATE"}:
            raise AdmissionError("OWNER_APPROVAL_REQUIRED")
        if not state.budget_authorized:
            raise AdmissionError("BUDGET_DENIED")
        if operation == "SOURCE_BRANCH_UPDATE" and not state.source_writes_enabled:
            raise AdmissionError("CUTOVER_NOT_APPROVED")
        if operation == "DRIVE_REVISION_UPDATE" and not state.document_writes_enabled:
            raise AdmissionError("CUTOVER_NOT_APPROVED")

    def _load(self, request: dict) -> State:
        if self.authority is None:
            raise AdmissionError("GATEWAY_UNAVAILABLE")
        try:
            return self.authority.load(request["job_id"], request["grant_id"])
        except (ConnectionError, TimeoutError, LookupError):
            raise AdmissionError("GATEWAY_UNAVAILABLE") from None

    def _github(self, state: State, repo: str, branch: str, base: str,
                paths: list[str], *, patch: bytes | None = None,
                tree_sha: str | None = None):
        if self.git is None:
            raise AdmissionError("GATEWAY_UNAVAILABLE")
        grant = state.grant
        if repo != grant["repo_id"] or branch != state.work_branch:
            raise AdmissionError("INVALID_SCOPE")
        # Only assigned noncanonical branches can be updated by workers.
        if (branch in {"main", "master", "develop", "development"} or
                not branch.startswith(f"studio/workorders/{grant['job_id']}/")):
            raise AdmissionError("INVALID_SCOPE")
        if base != state.source_base_sha or base != grant["source_base_sha"]:
            raise AdmissionError("REBASE_REQUIRED")
        try:
            snap = self.git.snapshot(repo, branch)
        except (TimeoutError, ConnectionError, LookupError):
            raise AdmissionError("GATEWAY_UNAVAILABLE") from None
        if snap.repo_id != repo or snap.branch != branch:
            raise AdmissionError("INVALID_SCOPE")
        if snap.head_sha != base:
            raise AdmissionError("REBASE_REQUIRED")
        proposed = snap.entries
        if patch is not None:
            try:
                inspected = self.git.inspect_patch(repo, base, patch)
            except (TimeoutError, ConnectionError, LookupError):
                raise AdmissionError("GATEWAY_UNAVAILABLE") from None
            if (inspected.patch_sha256 != hashlib.sha256(patch).hexdigest() or
                tree_sha != inspected.proposed_tree_sha256 or
                set(inspected.changed_paths) != set(paths) or
                len(paths) != len(inspected.changed_paths)):
                raise AdmissionError("SOURCE_SHA_MISMATCH")
            proposed = inspected.proposed_entries
        denied = validate_paths(paths, grant["owned_path_prefixes"], snap.entries, proposed)
        if denied:
            raise AdmissionError(denied)

    def _audit(self, event: str, status: str, code: str | None, request_id: str,
               principal: Principal | None) -> str:
        if self.audit is None:
            raise AdmissionError("GATEWAY_UNAVAILABLE")
        audit_id = "EVT-" + secrets.token_hex(16)
        # Deliberately exclude credential_ref, artifact bytes, source, tokens, cases.
        self.audit.emit({"event_id": audit_id, "event": event, "result": status,
                         "denial_code": code, "request_id": request_id,
                         "principal_id": principal.principal_id if principal else None,
                         "at": utcstr(self.clock())})
        return audit_id

    def evaluate(self, request: object, principal: Principal | None) -> dict:
        request_id = request.get("request_id", "invalid-request") if isinstance(request, dict) else "invalid-request"
        policy_rev = request.get("policy_revision", "policy:unknown") if isinstance(request, dict) else "policy:unknown"
        code = None
        state = None
        try:
            validate(request, "AUTH-V16/EVALUATE/1")
            if principal is None or principal.auth_method != "MTLS":
                raise AdmissionError("IDENTITY_UNVERIFIED")
            state = self._load(request)
            self._check_state(request, principal, state, request["operation"])
            if request["operation"] == "SOURCE_BRANCH_UPDATE":
                self._github(state, request["repo_id"], request["target_branch"],
                             request["source_base_sha"], request["target_paths"])
            else:
                if request["target_paths"] or request["target_branch"] is not None:
                    raise AdmissionError("INVALID_SCOPE")
                if self.drive is None:
                    raise AdmissionError("GATEWAY_UNAVAILABLE")
            if state.qa is None or state.qa.verdict != "PASS":
                raise AdmissionError("QA_PENDING")
            if state.qa.reviewer_principal_id == principal.principal_id:
                raise AdmissionError("SELF_REVIEW")
        except (ContractError, AdmissionError) as exc:
            code = exc.code
        except Exception:
            code = "GATEWAY_UNAVAILABLE"
        try:
            event_id = self._audit("AUTH", "DENY" if code else "ALLOW", code,
                                   str(request_id)[:120], principal)
        except Exception:
            code, event_id = "GATEWAY_UNAVAILABLE", "EVT-unavailable"
        result = {"schema_id": "AUTH-V16/DECISION/1", "request_id": request_id,
                  "decision": "DENY" if code else "ALLOW", "denial_code": code,
                  "verified_principal_id": principal.principal_id if principal and principal.auth_method == "MTLS" else None,
                  "grant_id": request.get("grant_id") if state is not None else None,
                  "policy_revision": policy_rev if isinstance(policy_rev, str) and len(policy_rev) >= 4 else "policy:unknown",
                  "evaluated_at": utcstr(self.clock()), "audit_event_id": event_id}
        # Defensive: never emit an authority artifact that breaks the frozen schema.
        try:
            return validate(result, "AUTH-V16/DECISION/1")
        except ContractError:
            result.update(request_id="invalid-request", grant_id=None,
                          policy_revision="policy:unknown", decision="DENY",
                          denial_code="MALFORMED_REQUEST")
            return result

    def prepare(self, intent: object, artifact: bytes, principal: Principal | None) -> dict:
        """Admission + atomic reservation only. NO provider write is performed."""
        code = None
        state = None
        provider = "GITHUB"
        try:
            validate(intent, "PUBLISH-V16/INTENT/1")
            if not isinstance(artifact, bytes) or not artifact or len(artifact) > 16 * 1024 * 1024:
                raise AdmissionError("INVALID_SCOPE")
            if hashlib.sha256(artifact).hexdigest() != intent["artifact_sha256"]:
                raise AdmissionError("SOURCE_SHA_MISMATCH")
            if principal is None or principal.auth_method != "MTLS":
                raise AdmissionError("IDENTITY_UNVERIFIED")
            state = self._load(intent)
            target = intent["target"]
            if not isinstance(target, dict):
                raise AdmissionError("MALFORMED_REQUEST")
            provider = target.get("provider", "GITHUB")
            if provider == "GITHUB":
                required = {"provider", "operation", "repo_id", "work_branch", "source_base_sha",
                            "patch_sha256", "proposed_tree_sha256", "changed_paths"}
                operation = "SOURCE_BRANCH_UPDATE"
            elif provider == "GOOGLE_DRIVE":
                required = {"provider", "operation", "file_id", "expected_revision", "exported_sha256",
                            "document_owner_ref"}
                operation = "DRIVE_REVISION_UPDATE"
            else:
                raise AdmissionError("MALFORMED_REQUEST")
            if set(target) != required or target["operation"] != operation:
                raise AdmissionError("MALFORMED_REQUEST")
            self._check_state(intent, principal, state, operation)
            if not state.evidence_sha256 or intent["evidence_sha256"] != state.evidence_sha256:
                raise AdmissionError("QA_PENDING")
            qa = intent["independent_qa"]
            trusted_qa = state.qa
            if not trusted_qa or trusted_qa.verdict != "PASS":
                raise AdmissionError("QA_PENDING")
            if trusted_qa.reviewer_principal_id == principal.principal_id:
                raise AdmissionError("SELF_REVIEW")
            if (any(qa[k] != getattr(trusted_qa, k) for k in (
                "review_id", "reviewer_principal_id", "source_sha", "subject_sha256", "evidence_ref", "verdict"))
                or qa["subject_sha256"] not in {intent["artifact_sha256"],
                    target.get("proposed_tree_sha256"), target.get("exported_sha256")}):
                raise AdmissionError("QA_PENDING")
            if qa["source_sha"] != state.source_base_sha:
                raise AdmissionError("QA_PENDING")
            if provider == "GITHUB":
                if not isinstance(target["changed_paths"], list) or any(not isinstance(p, str) for p in target["changed_paths"]):
                    raise AdmissionError("MALFORMED_REQUEST")
                if target["patch_sha256"] != intent["artifact_sha256"]:
                    raise AdmissionError("SOURCE_SHA_MISMATCH")
                self._github(state, target["repo_id"], target["work_branch"],
                             target["source_base_sha"], target["changed_paths"],
                             patch=artifact, tree_sha=target["proposed_tree_sha256"])
            else:
                if self.drive is None or target["file_id"] != state.drive_file_id or target["document_owner_ref"] != state.drive_document_owner_ref:
                    raise AdmissionError("INVALID_SCOPE")
                if hashlib.sha256(artifact).hexdigest() != target["exported_sha256"]:
                    raise AdmissionError("SOURCE_SHA_MISMATCH")
                if target["expected_revision"] != state.drive_expected_revision or self.drive.revision(target["file_id"]) != target["expected_revision"]:
                    raise AdmissionError("DOCUMENT_CONFLICT")
            if self.authority is None:
                raise AdmissionError("GATEWAY_UNAVAILABLE")
            result = self.authority.reserve(intent, principal.principal_id, state.state_revision, canonical_hash(intent))
            if result not in ("COMMITTED", "DUPLICATE"):
                raise AdmissionError(result if isinstance(result, str) else "GATEWAY_UNAVAILABLE")
        except (ContractError, AdmissionError) as exc:
            code = exc.code
        except Exception:
            code = "GATEWAY_UNAVAILABLE"
        try:
            event_id = self._audit("PUBLISH_PREPARE", "BLOCKED" if code else "PREPARED",
                                   code, intent.get("publication_id", "invalid-request") if isinstance(intent, dict) else "invalid-request", principal)
        except Exception:
            code, event_id = "GATEWAY_UNAVAILABLE", "EVT-unavailable"
        raw_pub = intent.get("publication_id") if isinstance(intent, dict) else None
        raw_key = intent.get("idempotency_key") if isinstance(intent, dict) else None
        safe_pub = raw_pub if isinstance(raw_pub, str) and 0 < len(raw_pub) <= 180 else "invalid-request"
        safe_key = raw_key if isinstance(raw_key, str) and re.fullmatch("[a-f0-9]{64}", raw_key) else "0" * 64
        receipt = {"schema_id": "PUBLISH-V16/READBACK/1",
                   "publication_id": safe_pub,
                   "idempotency_key": safe_key,
                   "provider": provider if provider in {"GITHUB", "GOOGLE_DRIVE"} else "GITHUB",
                   "status": "BLOCKED" if code else "PREPARED", "denial_code": code,
                   "observed": None, "provider_request_id": None, "verified_at": None,
                   "audit_event_id": event_id}
        return receipt

    def reconcile(self, publication_id: str, idempotency_key: str,
                  principal: Principal | None) -> dict:
        """Read the external provider (WG-G) and confirm before DB effect; no inline retry."""
        # Never turn malformed URL/body fields into an authority store lookup.
        if (not isinstance(publication_id, str) or not publication_id or len(publication_id) > 180
                or not isinstance(idempotency_key, str)
                or not re.fullmatch(r"[a-f0-9]{64}", idempotency_key)):
            return self._receipt("invalid-request", "0" * 64, "GITHUB",
                                 "BLOCKED", "MALFORMED_REQUEST", None, "EVT-invalid-request")
        intent = None
        state = None
        observed = None
        status, code, provider_name = "BLOCKED", "GATEWAY_UNAVAILABLE", "GITHUB"
        try:
            if principal is None or principal.auth_method != "MTLS" or self.authority is None:
                raise AdmissionError("IDENTITY_UNVERIFIED")
            intent = self.authority.recorded(publication_id, idempotency_key)
            if intent is None:
                raise AdmissionError("ACCESS_REQUIRED")
            validate(intent, "PUBLISH-V16/INTENT/1")
            if principal.principal_id != intent["principal_id"]:
                raise AdmissionError("IDENTITY_UNVERIFIED")
            state = self._load(intent)
            target = intent["target"]
            provider_name = target["provider"]
            self._check_state(intent, principal, state, target["operation"])
            qa = intent["independent_qa"]
            trusted_qa = state.qa
            if (not trusted_qa or trusted_qa.verdict != "PASS" or
                trusted_qa.reviewer_principal_id == principal.principal_id or
                not state.evidence_sha256 or intent["evidence_sha256"] != state.evidence_sha256 or
                any(qa[k] != getattr(trusted_qa, k) for k in (
                    "review_id", "reviewer_principal_id", "source_sha", "subject_sha256", "evidence_ref", "verdict"))):
                raise AdmissionError("QA_PENDING")
            if provider_name == "GITHUB":
                if self.git is None:
                    raise AdmissionError("GATEWAY_UNAVAILABLE")
                observed = self.git.readback(intent)
            elif provider_name == "GOOGLE_DRIVE":
                if self.drive is None:
                    raise AdmissionError("GATEWAY_UNAVAILABLE")
                observed = self.drive.readback(intent)
            else:
                raise AdmissionError("INVALID_SCOPE")
            if observed is None:
                status, code = "EXTERNAL_OBSERVED", None
            elif provider_name == "GITHUB":
                required = {"repo_id": target["repo_id"], "work_branch": target["work_branch"],
                            "tree_sha256": target["proposed_tree_sha256"],
                            "artifact_sha256": intent["artifact_sha256"]}
                if (not isinstance(observed, dict) or set(observed) != set(required) | {"commit_sha"} or
                    any(observed.get(k) != v for k, v in required.items()) or
                    not isinstance(observed.get("commit_sha"), str) or len(observed["commit_sha"]) != 40):
                    status, code = "CONFLICT", "SOURCE_SHA_MISMATCH"
                else:
                    status, code = "VERIFIED", None
            else:
                required = {"file_id": target["file_id"], "exported_sha256": target["exported_sha256"]}
                if (not isinstance(observed, dict) or set(observed) != set(required) | {"revision"} or
                    any(observed.get(k) != v for k, v in required.items()) or
                    observed.get("revision") == target["expected_revision"]):
                    status, code = "CONFLICT", "DOCUMENT_CONFLICT"
                else:
                    status, code = "VERIFIED", None
            # Audit before confirming durable publication; deny on audit unavailable.
            event_id = self._audit("PUBLISH_RECONCILE", status, code, publication_id, principal)
            receipt = self._receipt(publication_id, idempotency_key, provider_name, status,
                                    code, observed, event_id)
            if status == "VERIFIED" and not self.authority.reconcile(
                    publication_id, idempotency_key, state.state_revision, receipt):
                raise AdmissionError("RETRYABLE_BLOCKED")
            if status == "CONFLICT":
                # The real PostgreSQL adapter persists conflicts and blocks future
                # provider retries. Older test doubles do not implement this hook.
                persist = getattr(self.authority, "mark_conflict", None)
                if persist is not None and not persist(
                        publication_id, idempotency_key, state.state_revision, receipt):
                    raise AdmissionError("RETRYABLE_BLOCKED")
            return validate(receipt, "PUBLISH-V16/READBACK/1")
        except (AdmissionError, ContractError) as exc:
            code = exc.code
        except Exception:
            code = "GATEWAY_UNAVAILABLE"
        try:
            event_id = self._audit("PUBLISH_RECONCILE", "BLOCKED", code, publication_id, principal)
        except Exception:
            event_id = "EVT-unavailable"
        return self._receipt(publication_id, idempotency_key, provider_name,
                             "BLOCKED", code, None, event_id)

    def _receipt(self, publication_id, idem, provider, status, code, observed, event_id):
        return {"schema_id": "PUBLISH-V16/READBACK/1", "publication_id": publication_id,
                "idempotency_key": idem, "provider": provider, "status": status,
                "denial_code": code, "observed": observed if status == "VERIFIED" else None,
                "provider_request_id": None, "verified_at": utcstr(self.clock()) if status == "VERIFIED" else None,
                "audit_event_id": event_id}
