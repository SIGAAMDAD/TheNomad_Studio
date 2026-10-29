"""WG-AM clean-room workforce qualification service.

No privileged Studio or Oversight side effects. Owner/grant/identity/receipt
verification ports are injected by an integrator, not inferred from request text.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Protocol, Tuple

from .contracts import (CapabilityEvidence, Decision, Dossier, GrantClaim,
                        HandoverPlan, Identity, Lifecycle, QualificationRequest,
                        Result, Role, TrainingEvidence)
from .store import LocalJournal, digest

_HEX = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def valid_id(value: str) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def valid_digest(value: str) -> bool:
    return isinstance(value, str) and bool(_HEX.fullmatch(value))


class IdentityReader(Protocol):
    def __call__(self, worker_id: str) -> Optional[Identity]: ...


class RoleReader(Protocol):
    def __call__(self, role_id: str) -> Optional[Role]: ...


class GrantVerifier(Protocol):
    def __call__(self, grant: GrantClaim, identity: Identity, now: int) -> bool: ...


class CapabilityVerifier(Protocol):
    def __call__(self, evidence: CapabilityEvidence, now: int) -> bool: ...


class TrainingVerifier(Protocol):
    def __call__(self, evidence: TrainingEvidence, now: int) -> bool: ...


@dataclass(frozen=True)
class WorkforcePorts:
    identity_reader: Optional[IdentityReader] = None
    role_reader: Optional[RoleReader] = None
    grant_verifier: Optional[GrantVerifier] = None
    capability_verifier: Optional[CapabilityVerifier] = None
    training_verifier: Optional[TrainingVerifier] = None
    clock: Callable[[], int] = lambda: int(time.time())


def _restored(data: dict) -> Decision:
    return Decision(
        recommendation_id=data['recommendation_id'], worker_id=data['worker_id'],
        action=Lifecycle(data['action']), status=Result(data['status']),
        reason_code=data['reason_code'], source_identity_epoch=data['source_identity_epoch'],
        source_assignment_epoch=data['source_assignment_epoch'],
        policy_revision=data['policy_revision'],
        evidence_digests=tuple(data['evidence_digests']),
        valid_until=data['valid_until'], target_role_id=data['target_role_id'],
        minimum_permissions=tuple(data['minimum_permissions']),
        verified_facts=tuple(data['verified_facts']), inferences=tuple(data['inferences']))


class WorkforceComponent:
    """Read-only Studio-facing suggestions and append-only local audit records.

    Source of active admission is *always* the injected Studio identity reader.
    No method of this class updates Studio roles, grants, shifts or leases.
    """

    operation = 'hr.qualify'
    contract_version = 'HR/WORKFORCE-RECOMMENDATION/1'

    def __init__(self, ports: WorkforcePorts, journal: Optional[LocalJournal] = None) -> None:
        self.ports = ports
        self.journal = journal if journal is not None else LocalJournal()

    def _decision(self, q: QualificationRequest, status: Result, code: str,
                  expiry: Optional[int] = None, permissions: Tuple[str, ...] = (),
                  facts: Tuple[str, ...] = (), evidence: Tuple[str, ...] = ()) -> Decision:
        return Decision(
            recommendation_id=q.request_id, worker_id=q.worker_id,
            action=q.action, status=status, reason_code=code,
            source_identity_epoch=q.identity_epoch,
            source_assignment_epoch=q.assignment_epoch,
            policy_revision=q.policy_revision, evidence_digests=evidence,
            valid_until=expiry, target_role_id=q.target_role_id,
            minimum_permissions=permissions, verified_facts=facts,
            inferences=('Proposed lifecycle action only; Studio authority unchanged',)
            if status is Result.RECOMMENDED else ())

    def recommend(self, q: QualificationRequest) -> Decision:
        """Deny invalid, stale, replayed or unverified qualifications.

        A valid result is a *recommendation*, never a worker admission or notice.
        No request content itself is considered a trusted owner grant or receipt.
        """
        now = self.ports.clock()
        request_hash = digest(q)
        if not valid_id(q.request_id) or not valid_id(q.worker_id) or not valid_id(q.target_role_id):
            return self._decision(q, Result.DENIED, 'MALFORMED_IDENTIFIER')
        old = self.journal.find_recommendation(q.request_id)
        if old:
            if old[0] != request_hash:
                return self._decision(q, Result.DENIED, 'DENIED_REPLAY_CONFLICT')
            prior = _restored(old[1])
            if prior.status == Result.RECOMMENDED and self._check(q, now).status != Result.RECOMMENDED:
                return self._decision(q, Result.DENIED, 'DENIED_REPLAY_NOT_CURRENT')
            return prior
        result = self._check(q, now)
        if not self.journal.write_recommendation(q.request_id, request_hash, q.worker_id, result, now):
            old = self.journal.find_recommendation(q.request_id)
            return _restored(old[1]) if old and old[0] == request_hash else self._decision(q, Result.DENIED, 'DENIED_REPLAY_CONFLICT')
        return result

    def _check(self, q: QualificationRequest, now: int) -> Decision:
        p = self.ports
        if not isinstance(q.action, Lifecycle) or not valid_id(q.current_role_id) or not valid_id(q.policy_revision):
            return self._decision(q, Result.DENIED, 'INVALID_CONTRACT')
        if q.identity_epoch < 0 or q.assignment_epoch < 0 or q.persona_revision < 0 or not valid_digest(q.persona_digest):
            return self._decision(q, Result.DENIED, 'INVALID_IDENTITY_BINDING')
        if p.identity_reader is None or p.role_reader is None:
            return self._decision(q, Result.UNSUPPORTED, 'MISSING_STUDIO_IDENTITY_OR_ROLE_PORT')
        try:
            i = p.identity_reader(q.worker_id)
            role = p.role_reader(q.target_role_id)
            current = p.role_reader(q.current_role_id)
        except Exception:
            return self._decision(q, Result.NOT_VERIFIED, 'IDENTITY_PROVIDER_FAILURE')
        if i is None or i.worker_id != q.worker_id:
            return self._decision(q, Result.DENIED, 'WRONG_IDENTITY')
        if i.retired:
            return self._decision(q, Result.DENIED, 'RETIRED_ID_REUSE')
        if i.identity_epoch != q.identity_epoch or i.assignment_epoch != q.assignment_epoch:
            return self._decision(q, Result.DENIED, 'STALE_EPOCH')
        if i.role_id != q.current_role_id or i.department_id != (current.department_id if current else None):
            return self._decision(q, Result.DENIED, 'STALE_ROLE_BINDING')
        if i.persona_revision != q.persona_revision or i.persona_digest != q.persona_digest:
            return self._decision(q, Result.DENIED, 'STALE_PERSONA_BINDING')
        if role is None or current is None or role.role_id != q.target_role_id or current.role_id != q.current_role_id or not valid_id(role.department_id):
            return self._decision(q, Result.DENIED, 'UNKNOWN_CANONICAL_ROLE')
        if q.action not in (Lifecycle.JOINER, Lifecycle.REINSTATEMENT) and not i.active:
            return self._decision(q, Result.DENIED, 'INACTIVE_STUDIO_IDENTITY')
        if q.action == Lifecycle.JOINER and i.active:
            return self._decision(q, Result.DENIED, 'JOINER_ALREADY_ACTIVE')
        if q.action == Lifecycle.REINSTATEMENT and i.active:
            return self._decision(q, Result.DENIED, 'REINSTATEMENT_ALREADY_ACTIVE')
        if q.action != Lifecycle.ROLE_CHANGE and q.target_role_id != q.current_role_id:
            return self._decision(q, Result.DENIED, 'ROLE_CHANGE_ACTION_REQUIRED')
        if q.grant is None:
            return self._decision(q, Result.DENIED, 'MISSING_OWNER_GRANT')
        g = q.grant
        if (not valid_id(g.grant_id) or g.worker_id != i.worker_id or
            g.identity_epoch != i.identity_epoch or g.action != q.action or
            g.target_role_id != role.role_id or g.policy_revision != q.policy_revision or
            g.target_privileges != role.privileges or g.expires_at <= now):
            return self._decision(q, Result.DENIED, 'INVALID_OWNER_GRANT_BINDING')
        if p.grant_verifier is None:
            return self._decision(q, Result.NOT_VERIFIED, 'OWNER_GRANT_VERIFIER_UNAVAILABLE')
        try:
            authorized = p.grant_verifier(g, i, now)
        except Exception:
            return self._decision(q, Result.NOT_VERIFIED, 'OWNER_GRANT_VERIFIER_FAILURE')
        if authorized is not True:
            return self._decision(q, Result.DENIED, 'OWNER_GRANT_NOT_AUTHENTICATED')
        if (not role.privileges.issubset(i.privileges) and q.action != Lifecycle.ROLE_CHANGE):
            return self._decision(q, Result.DENIED, 'UNAUTHORIZED_PRIVILEGE_ESCALATION')
        if q.action in (Lifecycle.TEMP_RESTRICTION, Lifecycle.RETIREMENT) and not valid_digest(q.reason_digest):
            return self._decision(q, Result.INSUFFICIENT_EVIDENCE, 'MISSING_OBJECTIVE_REASON')
        # Non-qualification transitions still require owner verification; qualification
        # and changes require comparable measured training/receipt evidence.
        requires_skill = q.action in (Lifecycle.JOINER, Lifecycle.QUALIFY, Lifecycle.PROBATION,
                                      Lifecycle.ACTIVE_REVIEW, Lifecycle.ROLE_CHANGE,
                                      Lifecycle.REINSTATEMENT)
        verified_digests = []
        expiry = [g.expires_at]
        facts = ['studio_identity_current', 'canonical_role_matched', 'owner_grant_verified']
        if requires_skill:
            if p.capability_verifier is None or p.training_verifier is None:
                return self._decision(q, Result.NOT_VERIFIED, 'EVIDENCE_VERIFIER_UNAVAILABLE')
            if not q.training:
                return self._decision(q, Result.INSUFFICIENT_EVIDENCE, 'NO_OBJECTIVE_TRAINING')
            seen_capabilities = set()
            seen_receipts = set()
            for c in q.capabilities:
                if (not valid_id(c.receipt_id) or c.receipt_id in seen_receipts or
                    c.worker_id != q.worker_id or c.identity_epoch != i.identity_epoch or
                    c.role_id != role.role_id or not valid_digest(c.source_digest) or
                    c.issued_at > now or c.expires_at <= now):
                    return self._decision(q, Result.DENIED, 'INVALID_OR_EXPIRED_CAPABILITY')
                seen_receipts.add(c.receipt_id)
                try:
                    if p.capability_verifier(c, now) is not True:
                        return self._decision(q, Result.NOT_VERIFIED, 'CAPABILITY_NOT_VERIFIED')
                except Exception:
                    return self._decision(q, Result.NOT_VERIFIED, 'CAPABILITY_PROVIDER_FAILURE')
                seen_capabilities.add(c.capability)
                verified_digests.append(c.source_digest)
                expiry.append(c.expires_at)
            if not role.required_capabilities.issubset(seen_capabilities):
                return self._decision(q, Result.INSUFFICIENT_EVIDENCE, 'MISSING_REQUIRED_CAPABILITY')
            seen_training = set()
            for t in q.training:
                if (not valid_id(t.record_id) or t.record_id in seen_training or
                    t.worker_id != q.worker_id or t.target_role_id != role.role_id or
                    t.evaluator_id == q.worker_id or not valid_id(t.evaluator_id) or
                    t.objective_result != 'PASS' or not valid_digest(t.source_digest) or
                    t.completed_at > now or t.expires_at <= now):
                    return self._decision(q, Result.DENIED, 'INVALID_OR_EXPIRED_TRAINING')
                seen_training.add(t.record_id)
                try:
                    if p.training_verifier(t, now) is not True:
                        return self._decision(q, Result.NOT_VERIFIED, 'TRAINING_NOT_VERIFIED')
                except Exception:
                    return self._decision(q, Result.NOT_VERIFIED, 'TRAINING_PROVIDER_FAILURE')
                verified_digests.append(t.source_digest)
                expiry.append(t.expires_at)
            facts.extend(('training_objective_pass_verified', 'capability_evidence_verified'))
        if q.reason_digest:
            if not valid_digest(q.reason_digest):
                return self._decision(q, Result.DENIED, 'MALFORMED_REASON_DIGEST')
            verified_digests.append(q.reason_digest)  # referenced, not independently verified
        return self._decision(q, Result.RECOMMENDED, 'OWNER_REVIEW_REQUIRED',
                              min(expiry), tuple(sorted(role.privileges)), tuple(facts),
                              tuple(sorted(set(verified_digests))))

    def dossier(self, worker_id: str) -> Dossier:
        if not valid_id(worker_id) or self.ports.identity_reader is None:
            return Dossier(worker_id, -1, '', -1, '', status=Result.NOT_VERIFIED)
        try:
            i = self.ports.identity_reader(worker_id)
        except Exception:
            i = None
        if i is None or i.worker_id != worker_id:
            return Dossier(worker_id, -1, '', -1, '', status=Result.NOT_VERIFIED)
        return Dossier(worker_id, i.identity_epoch, i.role_id, i.persona_revision,
                       i.persona_digest, tuple(_restored(r) for r in self.journal.history(worker_id)),
                       Result.NOT_VERIFIED)

    def handover(self, decision: Decision) -> HandoverPlan:
        """Suggest lease/ticket invalidation; cannot perform a role/assignment change."""
        old = self.journal.find_recommendation(decision.recommendation_id)
        ok = (decision.status == Result.RECOMMENDED and decision.action == Lifecycle.ROLE_CHANGE
              and old is not None and digest(old[1]) == digest(decision))
        if ok and (self.ports.identity_reader is None or self.ports.role_reader is None):
            ok = False
        try:
            i = self.ports.identity_reader(decision.worker_id) if ok else None
        except Exception:
            i = None
        ok = bool(ok and i and i.active and not i.retired and
                  i.identity_epoch == decision.source_identity_epoch and
                  i.assignment_epoch == decision.source_assignment_epoch and
                  decision.valid_until is not None and decision.valid_until > self.ports.clock())
        return HandoverPlan(
            worker_id=decision.worker_id, from_role_id=i.role_id if i else '',
            to_role_id=decision.target_role_id,
            expected_identity_epoch=decision.source_identity_epoch,
            expected_assignment_epoch=decision.source_assignment_epoch,
            proposed_next_assignment_epoch=decision.source_assignment_epoch + 1,
            expected_persona_revision=i.persona_revision if i else -1,
            proposed_next_persona_revision=(i.persona_revision+1) if i else -1,
            revoke_old_tickets=ok, fence_old_leases=ok, require_current_persona=ok,
            requires_studio_readback=True, status=Result.RECOMMENDED if ok else Result.DENIED)


def build_component(deps: Mapping[str, object]) -> WorkforceComponent:
    """P2 injectable facade; an empty mapping fails closed with UNSUPPORTED."""
    ports = WorkforcePorts(
        identity_reader=deps.get('identity_reader'), role_reader=deps.get('role_reader'),
        grant_verifier=deps.get('grant_verifier'),
        capability_verifier=deps.get('capability_verifier'),
        training_verifier=deps.get('training_verifier'),
        clock=deps.get('clock', lambda: int(time.time())))
    journal = deps.get('journal')
    if journal is not None and not isinstance(journal, LocalJournal):
        raise TypeError('provisional journal must be a LocalJournal; private integration needs a reviewed adapter')
    return WorkforceComponent(ports, journal)
