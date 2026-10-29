"""WG-AQ clean-room portable HR operations, with all privileged effects denied by default.

The in-memory state is a deterministic local test adapter, not confidential production
storage. Every private read/mutation requires a caller-supplied, case-scoped verifier.
No external network, filesystem, key generation, signing, owner grant, or model wake.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from typing import Callable, Mapping, Optional

from .models import (Alert, ArchiveManifest, ArchiveState, AuditSummary, CaseRecord,
                     Investigator, P0Proof, RestoreReceipt, Result, ResultCode,
                     RetentionRule, SealedIntake, digest_ok)


def canonical_digest(obj: dict) -> str:
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode("utf-8")).hexdigest()


def _quantile(samples: list[float], portion: float) -> Optional[float]:
    if not samples:
        return None
    ordered = sorted(samples)
    # nearest rank, deterministic for small-sample evidence
    import math
    return ordered[max(0, math.ceil(len(ordered) * portion) - 1)]


class OperationsService:
    """Local isolated state/ports; integration replaces store and verifies every port.

    Ports are callables injected in deps, not claimed authentic. A missing port is a
    hard denial. A port exception is a denial with a metadata-only alert/fault.
    """

    def __init__(self, deps: Mapping[str, object] | None = None) -> None:
        d = dict(deps or {})
        self._authorize = d.get("authorize_case")
        self._custody = d.get("verify_sealed_receipt")
        self._key_available = d.get("private_key_available")
        self._alert_sender = d.get("send_owner_alert")
        self._alert_ack = d.get("verify_alert_ack")
        self._policy = d.get("verify_retention_policy")
        self._signature = d.get("verify_archive_signature")
        self._ciphertext_proof = d.get("verify_ciphertext_encryption")
        self._witness = d.get("verify_restore_witness")
        self._p0_policy = d.get("verify_p0_policy_proof")
        self._gateway_preapproval = d.get("verify_gateway_preapproval")
        self.queue_cap = int(d.get("queue_cap", 1024))
        self.check_interval_seconds = int(d.get("check_interval_seconds", 3600))
        if self.queue_cap < 1 or self.check_interval_seconds < 1:
            raise ValueError("queue cap/check interval must be positive")
        self.cases: dict[str, CaseRecord] = {}  # restricted mock store
        self.events: dict[str, tuple[str, str]] = {}  # event ID -> case/digest, idempotent
        self.alerts: dict[str, Alert] = {}
        self.archives: dict[str, ArchiveState] = {}
        self.faults: Counter[str] = Counter()
        self.triage_ms: list[float] = []
        self._p0_replay: dict[str, str] = {}
        self._last_audit_at: Optional[int] = None
        self._host_online: Optional[bool] = None

    def _check(self, port: object, *args: object) -> bool:
        if not callable(port):
            return False
        try:
            return port(*args) is True
        except Exception:
            self.faults["PROVIDER_FAILURE"] += 1
            return False

    def _allowed(self, principal: str, case_id: str, action: str, now: int) -> bool:
        return (bool(principal) and isinstance(now, int) and now >= 0
                and self._check(self._authorize, principal, case_id, action, now))

    def _alert(self, kind: str, timestamp: int, reference: str = "") -> str:
        # Stable nonrevealing ID and fixed-kind only. Opaque reference is hashed.
        if kind not in {"UNVERIFIED_CUSTODY", "NO_PRIVATE_KEY", "NO_INVESTIGATOR",
                        "OVERDUE_CASE", "HIGH_RISK", "MISSED_AUDIT", "HOST_OUTAGE",
                        "SOURCE_COLLISION", "RESTORE_FAILURE", "ARCHIVE_FAILURE",
                        "P0_REQUEST", "BACKPRESSURE", "PROVIDER_FAILURE"}:
            raise ValueError("unrecognized alert classification")
        ident = canonical_digest({"kind": kind, "ref": reference})
        alert_id = "al_" + ident[:32]
        if alert_id not in self.alerts:
            self.alerts[alert_id] = Alert(alert_id, kind, timestamp)
        return alert_id

    def intake(self, sealed: SealedIntake, now: int) -> Result:
        """Only seal references are retained. No caller privilege is inferred.

        A valid sealed receipt can be held PENDING with unavailable custody/key.
        Beyond configured cap, caller retains its sealed receipt; explicit retry is
        required and no success/durable-storage promise is made.
        """
        if not sealed.valid() or not isinstance(now, int) or now < sealed.received_at:
            return Result(ResultCode.DENIED, reason="INVALID_ENVELOPE")
        event_fingerprint = canonical_digest(asdict(sealed))
        previous = self.events.get(sealed.event_id)
        if previous is not None:
            if previous == (sealed.case_id, event_fingerprint):
                return Result(ResultCode.PENDING, sealed.case_id, "IDEMPOTENT_RECEIPT")
            self.faults["SOURCE_COLLISION"] += 1
            self._alert("SOURCE_COLLISION", now, sealed.event_id)
            return Result(ResultCode.CONFLICT, reason="EVENT_ID_COLLISION")
        if sealed.case_id in self.cases:
            self.faults["SOURCE_COLLISION"] += 1
            self._alert("SOURCE_COLLISION", now, sealed.case_id)
            return Result(ResultCode.CONFLICT, reason="CASE_ID_COLLISION")
        if len(self.cases) >= self.queue_cap:
            self.faults["BACKPRESSURE"] += 1
            self._alert("BACKPRESSURE", now)
            return Result(ResultCode.BACKPRESSURE, reason="RETAIN_SEALED_RECEIPT_AND_RETRY")
        verified = self._check(self._custody, sealed.sealed_receipt_sha256,
                               sealed.ciphertext_sha256)
        rec = CaseRecord(sealed, custody_verified=verified, last_checked_at=now)
        self.cases[sealed.case_id] = rec
        self.events[sealed.event_id] = (sealed.case_id, event_fingerprint)
        if not verified:
            self._alert("UNVERIFIED_CUSTODY", now, sealed.case_id)
        if not self._check(self._key_available):
            self.faults["NO_PRIVATE_KEY"] += 1
            self._alert("NO_PRIVATE_KEY", now)
        if sealed.severity >= 3:
            self._alert("HIGH_RISK", now, sealed.case_id)
        return Result(ResultCode.PENDING, sealed.case_id, "SEALED_METADATA_ONLY")

    def route(self, principal: str, case_id: str, investigators: tuple[Investigator, ...],
              now: int) -> Result:
        rec = self.cases.get(case_id)
        if rec is None:
            return Result(ResultCode.DENIED, reason="UNKNOWN_CASE")
        if not self._allowed(principal, case_id, "assign", now):
            self.faults["CASE_ACCESS_DENIAL"] += 1
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS, reason="CASE_SCOPE_REQUIRED")
        if not rec.custody_verified or not self._check(self._key_available):
            self._alert("NO_PRIVATE_KEY", now)
            return Result(ResultCode.PENDING, case_id, "SEALED_PENDING_VERIFICATION")
        if rec.assigned_investigator:
            return Result(ResultCode.PENDING, case_id, "ALREADY_ASSIGNED")
        # Explicit ordered input from a trusted workforce port; no phantom reviewer.
        eligible = [i for i in investigators if i.active and i.epoch > 0 and 0 <= i.current_load < i.load_limit
                    and rec.sealed.retention_class in i.clearance
                    and rec.sealed.subject_token not in i.conflicted_subjects
                    and i.investigator_id != rec.sealed.subject_token
                    and self._allowed(i.investigator_id, case_id, "investigate", now)]
        if not eligible:
            self.faults["BLOCKED_INDEPENDENCE"] += 1
            self._alert("NO_INVESTIGATOR", now, case_id)
            return Result(ResultCode.BLOCKED_INDEPENDENCE, case_id, "PENDING_INDEPENDENT_REVIEW")
        eligible.sort(key=lambda i: (i.current_load / i.load_limit, i.investigator_id))
        investigator = eligible[0]
        rec.assigned_investigator = investigator.investigator_id
        rec.assigned_epoch = investigator.epoch
        rec.state = "IN_TRIAGE"
        self.triage_ms.append(float((now - rec.sealed.received_at) * 1000))
        return Result(ResultCode.ACCEPTED, case_id, "ASSIGNED")

    def revoke_investigator(self, principal: str, case_id: str,
                            investigator_id: str, now: int) -> Result:
        rec = self.cases.get(case_id)
        if rec is None or not self._allowed(principal, case_id, "assign", now):
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        if rec.assigned_investigator != investigator_id:
            return Result(ResultCode.DENIED, reason="STALE_INVESTIGATOR")
        rec.assigned_investigator = None
        rec.assigned_epoch = 0
        rec.state = "PENDING"
        self._alert("NO_INVESTIGATOR", now, case_id)
        return Result(ResultCode.PENDING, case_id, "REVOKED_ASSIGNMENT")

    def case_status(self, principal: str, case_id: str, now: int) -> Result:
        rec = self.cases.get(case_id)
        if rec is None or not self._allowed(principal, case_id, "read", now):
            self.faults["CASE_ACCESS_DENIAL"] += 1
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        return Result(ResultCode.PENDING if rec.state == "PENDING" else ResultCode.ACCEPTED,
                      case_id, rec.state)

    def record_fault(self, kind: str, now: int) -> None:
        if kind not in {"MISSED_AUDIT", "NO_PRIVATE_KEY", "HOST_OUTAGE",
                        "SOURCE_COLLISION", "RESTORE_FAILURE", "PROVIDER_FAILURE"}:
            raise ValueError("fixed fault kinds only")
        self.faults[kind] += 1
        self._alert(kind, now)

    def host_status(self, online: bool, now: int) -> None:
        self._host_online = online
        if online is False:
            self.record_fault("HOST_OUTAGE", now)

    def audit(self, now: int) -> AuditSummary:
        """Owner-safe aggregation; audit never exposes private case IDs or narratives."""
        if not isinstance(now, int) or now < 0:
            raise ValueError("invalid time")
        if self._last_audit_at is not None and now < self._last_audit_at:
            raise ValueError("time rollback")
        if self._last_audit_at is not None and now - self._last_audit_at > self.check_interval_seconds:
            self.record_fault("MISSED_AUDIT", now)
        self._last_audit_at = now
        overdue = 0
        for rec in self.cases.values():
            if now - rec.last_checked_at > self.check_interval_seconds:
                overdue += 1
                self._alert("OVERDUE_CASE", now, rec.sealed.case_id)
            rec.last_checked_at = now
        total = len(self.cases)
        oldest = max((max(0, now - r.sealed.received_at) for r in self.cases.values()), default=0)
        assigned = sum(r.assigned_investigator is not None for r in self.cases.values())
        pending = sum(r.state == "PENDING" for r in self.cases.values())
        rolling = sum(now - 3600 < r.sealed.received_at <= now
                      for r in self.cases.values())
        statuses = ("LOCAL_ONLY", "NOT_VERIFIED_EXTERNAL")
        return AuditSummary(total, pending, total - assigned, oldest, overdue,
                            sum(r.sealed.severity >= 3 for r in self.cases.values()),
                            round(100 * assigned / total, 2) if total else 0.0,
                            len(self.events), float(rolling),
                            sum(a.state != "ACKNOWLEDGED" for a in self.alerts.values()),
                            dict(sorted(self.faults.items())), statuses,
                            _quantile(self.triage_ms, .50), _quantile(self.triage_ms, .95),
                            _quantile(self.triage_ms, .99))

    def deliver_alerts(self, now: int) -> Result:
        if not callable(self._alert_sender):
            return Result(ResultCode.UNSUPPORTED, reason="OWNER_CHANNEL_NOT_CONFIGURED")
        delivered = 0
        for alert in self.alerts.values():
            if alert.state == "ACKNOWLEDGED":
                continue
            alert.attempts += 1
            # Idempotency belongs to alert_id; not a fabricated delivery receipt.
            try:
                ok = self._alert_sender(alert.alert_id, alert.kind, alert.created_at) is True
            except Exception:
                ok = False
                self.faults["PROVIDER_FAILURE"] += 1
            if ok:
                alert.state = "SENT_UNACKNOWLEDGED"
                delivered += 1
        return Result(ResultCode.PENDING, reason=f"{delivered}_SENT_UNACKNOWLEDGED")

    def acknowledge_alert(self, alert_id: str, proof_ref: str, now: int) -> Result:
        alert = self.alerts.get(alert_id)
        if alert is None or not self._check(self._alert_ack, alert_id, proof_ref, now):
            return Result(ResultCode.DENIED, reason="UNVERIFIED_OWNER_ACK")
        alert.state = "ACKNOWLEDGED"
        return Result(ResultCode.ACCEPTED, alert_id, "INDEPENDENT_ACK")

    def stage_archive(self, principal: str, case_id: str, ciphertext: bytes,
                      rule: RetentionRule, now: int, signature_ref: str = "") -> Result:
        """Stages ciphertext only; never makes archive publishable or deletes source."""
        rec = self.cases.get(case_id)
        if rec is None or not self._allowed(principal, case_id, "archive", now):
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        if (not rec.custody_verified or not isinstance(ciphertext, bytes) or not ciphertext
                or not digest_ok(rule.policy_digest) or not rule.approved
                or rule.retention_class != rec.sealed.retention_class
                or rule.minimum_age_seconds < 1
                or not self._check(self._policy, rule)
                or not self._check(self._ciphertext_proof, case_id, sha256(ciphertext).hexdigest())):
            self._alert("ARCHIVE_FAILURE", now, case_id)
            return Result(ResultCode.DENIED, reason="ARCHIVE_POLICY_OR_CUSTODY_UNVERIFIED")
        cipherhash = sha256(ciphertext).hexdigest()
        if cipherhash != rec.sealed.ciphertext_sha256:
            self._alert("ARCHIVE_FAILURE", now, case_id)
            self.faults["ARCHIVE_FAILURE"] += 1
            return Result(ResultCode.QUARANTINED, case_id, "CIPHERTEXT_HASH_MISMATCH")
        descriptor = {"case_id": case_id, "ciphertext_sha256": cipherhash,
                      "retention_class": rule.retention_class,
                      "retention_policy_sha256": rule.policy_digest,
                      "staged_at": now, "schema": "HR/ENCRYPTED-ARCHIVE/1"}
        manhash = canonical_digest(descriptor)
        archive_id = "ar_" + manhash[:32]
        if rec.archive_id and rec.archive_id != archive_id:
            return Result(ResultCode.CONFLICT, reason="EXISTING_ARCHIVE")
        if archive_id not in self.archives:
            manifest = ArchiveManifest(archive_id, case_id, cipherhash, manhash,
                                       rule.retention_class, rule.policy_digest,
                                       now, signature_ref or None)
            self.archives[archive_id] = ArchiveState(manifest, ciphertext, principal)
            rec.archive_id = archive_id
        return Result(ResultCode.PENDING, archive_id, "NOT_SIGNED_OR_RESTORED")

    def verify_archive(self, principal: str, archive_id: str, now: int) -> Result:
        state = self.archives.get(archive_id)
        if state is None or not self._allowed(principal, state.manifest.case_id, "archive", now):
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        m = state.manifest
        descriptor = {"case_id": m.case_id, "ciphertext_sha256": m.ciphertext_sha256,
                      "retention_class": m.retention_class,
                      "retention_policy_sha256": m.retention_policy_sha256,
                      "staged_at": m.staged_at, "schema": "HR/ENCRYPTED-ARCHIVE/1"}
        if (sha256(state.ciphertext).hexdigest() != m.ciphertext_sha256
                or canonical_digest(descriptor) != m.manifest_sha256):
            state.quarantined = True
        if state.quarantined:
            return Result(ResultCode.QUARANTINED, archive_id, "INTEGRITY_FAILURE")
        if not state.manifest.signature_ref or not callable(self._signature):
            return Result(ResultCode.NOT_VERIFIED, archive_id, "SIGNATURE_VERIFIER_UNAVAILABLE")
        if not self._check(self._signature, state.manifest):
            self._alert("ARCHIVE_FAILURE", now, archive_id)
            return Result(ResultCode.DENIED, archive_id, "SIGNATURE_REJECTED")
        state.signature_verified = True
        return Result(ResultCode.PENDING, archive_id, "AWAITING_INDEPENDENT_RESTORE")

    def verify_restore(self, principal: str, receipt: RestoreReceipt, now: int) -> Result:
        state = self.archives.get(receipt.archive_id)
        if state is None or not self._allowed(principal, state.manifest.case_id, "restore", now):
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        m = state.manifest
        if (state.quarantined or not state.signature_verified or not receipt.restored_ok
                or receipt.manifest_sha256 != m.manifest_sha256
                or receipt.ciphertext_sha256 != m.ciphertext_sha256
                or not receipt.witness_ref or receipt.witness_ref == state.staged_by
                or receipt.witness_epoch <= 0
                or not self._check(self._witness, receipt)):
            state.quarantined = True
            self.record_fault("RESTORE_FAILURE", now)
            return Result(ResultCode.QUARANTINED, receipt.archive_id, "RESTORE_NOT_VERIFIED")
        state.restore_verified = True
        return Result(ResultCode.ACCEPTED, receipt.archive_id, "INDEPENDENT_MACHINE_RESTORE")

    def archive_publication_readiness(self, principal: str, archive_id: str,
                                      now: int) -> Result:
        """Pure gate only: publisher port is a separate P2 boundary."""
        state = self.archives.get(archive_id)
        if state is None or not self._allowed(principal, state.manifest.case_id, "publish", now):
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        if state.quarantined:
            return Result(ResultCode.QUARANTINED, archive_id, "ARCHIVE_QUARANTINED")
        if not state.signature_verified or not state.restore_verified:
            return Result(ResultCode.NOT_VERIFIED, archive_id, "ARCHIVE_NOT_INDEPENDENTLY_VERIFIED")
        return Result(ResultCode.PENDING, archive_id, "READY_EXTERNAL_PUBLISH_NOT_EXECUTED")

    def next_case(self, principal: str, now: int) -> Result:
        """Restricted deterministic queue, aging eventually overrides priority."""
        eligible = [r for r in self.cases.values() if r.state == "PENDING"
                    and self._allowed(principal, r.sealed.case_id, "assign", now)]
        if not eligible:
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS, reason="NO_AUTHORIZED_PENDING_CASE")
        eligible.sort(key=lambda r: (-(r.sealed.severity * 120 +
                              2 * (max(0, now - r.sealed.received_at) / 3600) ** 2),
                              r.sealed.received_at, r.sealed.case_id))
        return Result(ResultCode.PENDING, eligible[0].sealed.case_id, "QUEUE_CANDIDATE")

    def authorize_retention_deletion(self, principal: str, archive_id: str,
                                     rule: RetentionRule, now: int,
                                     owner_approval_ref: str,
                                     owner_approval_verifier: object = None) -> Result:
        """Only calculates permission; performs no removal anywhere."""
        state = self.archives.get(archive_id)
        if state is None or not self._allowed(principal, state.manifest.case_id, "delete", now):
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        rec = self.cases[state.manifest.case_id]
        valid = (not state.quarantined and state.signature_verified and state.restore_verified
                 and not rec.custody_hold and rule.approved
                 and rule.retention_class == state.manifest.retention_class
                 and rule.policy_digest == state.manifest.retention_policy_sha256
                 and now >= state.manifest.staged_at + rule.minimum_age_seconds
                 and self._check(self._policy, rule)
                 and bool(owner_approval_ref)
                 and self._check(owner_approval_verifier, archive_id, owner_approval_ref, now))
        if not valid:
            return Result(ResultCode.DENIED, archive_id, "RETENTION_OR_OWNER_GATE_UNSATISFIED")
        return Result(ResultCode.ACCEPTED, archive_id, "ADVISORY_ELIGIBLE_NO_DELETE")

    def update_custody_hold(self, principal: str, case_id: str, hold: bool,
                            now: int, policy_verifier: object = None) -> Result:
        rec = self.cases.get(case_id)
        if rec is None or not self._allowed(principal, case_id, "custody_hold", now):
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        if not self._check(policy_verifier, case_id, hold, now):
            return Result(ResultCode.DENIED, reason="HOLD_POLICY_UNVERIFIED")
        rec.custody_hold = bool(hold)
        return Result(ResultCode.ACCEPTED, case_id, "HOLD_UPDATED")

    def request_p0_isolation(self, principal: str, proof: P0Proof, now: int) -> Result:
        """Advisory narrow request only; gateway and owner must execute separately."""
        rec = self.cases.get(proof.case_id)
        if rec is None or not self._allowed(principal, proof.case_id, "p0", now):
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        if (proof.trigger not in {"CREDENTIAL_ABUSE", "SOURCE_TAMPERING",
                                   "CONFIDENTIAL_DISCLOSURE", "EVIDENCE_TAMPERING"}
                or rec.sealed.policy_trigger != proof.trigger or not rec.custody_verified
                or rec.sealed.severity < 4 or not digest_ok(proof.evidence_sha256)
                or proof.proposed_action not in {"REQUEST_LEASE_FENCE", "REQUEST_ACCESS_REVIEW"}
                or proof.target_scope not in {"WORKER_ID", "JOB_LEASE"}
                or proof.expires_at <= now or proof.expires_at > now + 3600
                or not proof.owner_approved_action_ref
                or not self._check(self._p0_policy, proof)
                or not self._check(self._gateway_preapproval, proof)):
            return Result(ResultCode.DENIED, reason="UNPROVEN_OR_UNAPPROVED_P0")
        fingerprint = canonical_digest(asdict(proof))
        old = self._p0_replay.get(proof.owner_approved_action_ref)
        if old is not None:
            return Result(ResultCode.DENIED if old != fingerprint else ResultCode.PENDING,
                          reason="REPLAY_DENIED" if old != fingerprint else "IDEMPOTENT_REQUEST")
        self._p0_replay[proof.owner_approved_action_ref] = fingerprint
        self._alert("P0_REQUEST", now, proof.case_id)
        return Result(ResultCode.PENDING, "p0_" + fingerprint[:32], "EXTERNAL_GATEWAY_NOT_EXECUTED")

    def recommend_restriction(self, principal: str, case_id: str,
                              proposed_action: str, now: int) -> Result:
        if case_id not in self.cases or not self._allowed(principal, case_id, "recommend", now):
            return Result(ResultCode.BLOCKED_PRIVATE_ACCESS)
        if proposed_action not in {"FLAG_FOR_OWNER", "REQUIRE_INDEPENDENT_REVIEW",
                                   "PAUSE_NEW_ASSIGNMENTS", "REQUEST_LEASE_FENCE",
                                   "REQUEST_ACCESS_REVIEW"}:
            return Result(ResultCode.DENIED, reason="ACTION_NOT_ALLOWED")
        return Result(ResultCode.PENDING,
                      "rc_" + canonical_digest({"case": case_id, "action": proposed_action})[:32],
                      "ADVISORY_ONLY_NO_ENFORCEMENT")


def build_component(deps: Mapping[str, object] | None = None) -> OperationsService:
    """P2 portable facade: no auto-registration, no privileged op admission."""
    return OperationsService(deps)
