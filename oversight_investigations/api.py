"""WG-AP provisional investigation, custody, finding, and independent appeal service.

Only metadata is processed. Ports are injected; missing trust authorities deny work.
This file contains no provider writes, decryption, signing or gateway registration.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Callable, Mapping
import re
import secrets

from .model import (
    Appeal, Case, Conflict, Denied, Evidence, EvidenceSubmission, Finding, IntakeReceipt,
    IntegrityFailure, Invalid, Principal, SourceReceipt, Unsupported,
    APPEAL_OUTCOMES, CLASSES, STATES,
)
from .store import InMemoryCaseStore, append_entry, assert_chain, digest, ZERO

SHA = re.compile(r'^[a-f0-9]{64}$')
OPAQUE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$')
NEXT = {
    'SEALED_RECEIVED': 'SCREENED',
    'SCREENED': 'TRIAGED',
    'TRIAGED': 'INVESTIGATING',
    'INVESTIGATING': 'EVIDENCE_READY',
    'EVIDENCE_READY': 'FINDINGS_DRAFT',
    'FINDINGS_DRAFT': 'SECOND_REVIEW',
    'SECOND_REVIEW': 'OWNER_DISPOSITION',
    'OWNER_DISPOSITION': 'CLOSED',
    'REOPENED': 'INVESTIGATING',
}
BLOCK_STATES = frozenset({'BLOCKED_ACCESS', 'BLOCKED_INDEPENDENCE',
                          'WAITING_EVIDENCE', 'WAITING_OWNER'})


def _hash(value: str) -> None:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise Invalid()


def _opaque(value: str) -> None:
    if not isinstance(value, str) or not OPAQUE.fullmatch(value):
        raise Invalid()


def _time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise Invalid()



def _safe_status(case: Case) -> dict:
    return {'case_id': case.case_id, 'state': case.state,
            'event_count': len(case.events), 'evidence_count': len(case.evidence),
            'appeal_count': len(case.appeals),
            'custody_sha256': case.custody[-1].record_sha256 if case.custody else ZERO}


class InvestigationService:
    """Private-boundary facade; adapters enforce real trust outside this module.

    ``credential`` is opaque to this service. It MUST be resolved by a trusted
    gateway identity port, not a name asserted in a model/chat message.
    """
    def __init__(self, deps: Mapping[str, object]) -> None:
        self.identity = deps.get('identity')
        self.auth = deps.get('authorization')
        self.clock = deps.get('clock')
        self.seal = deps.get('sealed_custody')
        self.source = deps.get('source_verification')
        self.intake = deps.get('intake_verification')
        self.owner = deps.get('owner_decision')
        self.eligibility = deps.get('investigator_eligibility')
        self.store = deps.get('case_store') or InMemoryCaseStore()
        self._no_external_effects = True

    def _actor(self, credential: object) -> tuple[Principal, datetime]:
        if self.identity is None or self.auth is None or self.clock is None:
            raise Unsupported()
        now = self.clock.now()
        _time(now)
        principal = self.identity.resolve(credential)
        if not isinstance(principal, Principal) or not principal.identity:
            raise Denied()
        _time(principal.valid_until)
        if principal.authorization_epoch < 0 or now >= principal.valid_until:
            raise Denied()
        return principal, now

    def _permit(self, principal: Principal, operation: str, case_id: str | None,
                scope: str | None, now: datetime) -> None:
        if self.auth is None or not self.auth.allow(principal, operation, case_id, scope, now):
            raise Denied()

    def _require_seal(self, evidence_ref: str, manifest: str,
                      principal: Principal, now: datetime) -> None:
        _opaque(evidence_ref)
        _hash(manifest)
        if self.seal is None:
            raise Unsupported()
        if not self.seal.verify_seal(evidence_ref, manifest, principal, now):
            raise Denied()

    def _mutate(self, case_id: str, credential: object, permission: str,
                request_id: str, intent: object, action: Callable[[Case, Principal, datetime], dict],
                scope: str | None = None) -> dict:
        _opaque(case_id)
        _opaque(request_id)
        principal, now = self._actor(credential)
        fingerprint = digest({'permission': permission, 'intent': intent})

        def change(case: Case) -> dict:
            # Every request, including an idempotent replay, rechecks current rights.
            self._permit(principal, permission, case_id, scope, now)
            if request_id in case.operations:
                old, response = case.operations[request_id]
                if fingerprint != old:
                    raise Conflict()
                return dict(response)
            result = action(case, principal, now)
            case.operations[request_id] = (fingerprint, dict(result))
            return result
        return self.store.update(case_id, change)

    def _log(self, case: Case, request_id: str, kind: str, principal: Principal,
             now: datetime, meta: object, *, custody: bool = False) -> None:
        append_entry(case.custody if custody else case.events,
                     request_id, kind, principal.identity, now, meta, case.state)

    def open_case(self, credential: object, *, intake_id: str, ciphertext_ref: str,
                  manifest_sha256: str, subject_worker_id: str | None,
                  reporter_id: str | None, policy_revision: int = 1) -> dict:
        _opaque(intake_id)
        if subject_worker_id is not None:
            _opaque(subject_worker_id)
        if reporter_id is not None:
            _opaque(reporter_id)
        if type(policy_revision) is not int or policy_revision < 1:
            raise Invalid()
        principal, now = self._actor(credential)
        self._permit(principal, 'OPEN_CASE', None, None, now)
        self._require_seal(ciphertext_ref, manifest_sha256, principal, now)
        if self.intake is None:
            raise Unsupported()
        receipt = self.intake.verify_intake(
            intake_id=intake_id, ciphertext_ref=ciphertext_ref,
            manifest_sha256=manifest_sha256,
            subject_worker_id=subject_worker_id, reporter_id=reporter_id,
            policy_revision=policy_revision, principal=principal, now=now)
        if not isinstance(receipt, IntakeReceipt):
            raise Denied()
        _time(receipt.source_occurred_at)
        _time(receipt.valid_until)
        if (receipt.intake_id, receipt.ciphertext_ref, receipt.manifest_sha256,
                receipt.subject_worker_id, receipt.reporter_id,
                receipt.policy_revision) != (intake_id, ciphertext_ref, manifest_sha256,
                                            subject_worker_id, reporter_id, policy_revision):
            raise Denied()
        if (now >= receipt.valid_until or not receipt.verifier_id or
                receipt.source_occurred_at > now):
            raise Denied()
        fingerprint = digest([ciphertext_ref, manifest_sha256, subject_worker_id,
                              reporter_id, policy_revision])

        def make() -> Case:
            case = Case('CASE-' + secrets.token_hex(16), 'SEALED_RECEIVED',
                        subject_worker_id, reporter_id, intake_id, manifest_sha256,
                        now, receipt.source_occurred_at, receipt.verifier_id,
                        policy_revision=policy_revision)
            self._log(case, intake_id, 'SEALED_INTAKE', principal, now,
                      {'seal': manifest_sha256,
                       'source_occurred_at': receipt.source_occurred_at,
                       'verifier_id': receipt.verifier_id})
            self._log(case, intake_id + ':custody', 'SEALED_RECEIVED', principal,
                      now, {'manifest': manifest_sha256}, custody=True)
            return case
        case, _created = self.store.create(intake_id, fingerprint, make)
        # An existing deduplicated intake must not disclose if the user lost access.
        self._permit(principal, 'READ_CASE', case.case_id, None, now)
        return _safe_status(case)

    def query_status(self, credential: object, case_id: str) -> dict:
        principal, now = self._actor(credential)
        case = self.store.read(case_id)
        self._permit(principal, 'READ_CASE', case_id, None, now)
        return _safe_status(case)

    def public_metrics(self) -> dict[str, int]:
        """No case IDs, identities, narratives, reviewer names, source refs or ciphertext."""
        return self.store.aggregate()

    def transition(self, credential: object, case_id: str, *, target: str,
                   request_id: str) -> dict:
        if target not in STATES:
            raise Invalid()
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            current = case.state
            if target != NEXT.get(current):
                raise Denied()
            if target == 'INVESTIGATING' and not case.investigator_id:
                raise Denied()
            if target == 'EVIDENCE_READY' and not case.evidence:
                raise Denied()
            if target == 'FINDINGS_DRAFT' and case.finding is None:
                raise Denied()
            if target == 'SECOND_REVIEW' and case.second_reviewer_id is None:
                raise Denied()
            if target == 'OWNER_DISPOSITION' and case.second_reviewer_id is None:
                raise Denied()
            if target == 'CLOSED' and not case.owner_decision_id:
                raise Denied()
            case.state = target
            self._log(case, request_id, 'STATE_TRANSITION', principal, now,
                      {'from': current, 'to': target})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'ADVANCE_CASE', request_id,
                            {'target': target}, action)

    def mark_blocked(self, credential: object, case_id: str, *, target: str,
                     request_id: str) -> dict:
        if target not in BLOCK_STATES:
            raise Invalid()
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            if case.state in BLOCK_STATES or case.state in ('CLOSED', 'APPEALED'):
                raise Denied()
            case.suspended_state = case.state
            case.state = target
            self._log(case, request_id, 'CASE_BLOCKED', principal, now, {'target': target})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'BLOCK_CASE', request_id,
                            {'target': target}, action)

    def resume(self, credential: object, case_id: str, *, request_id: str) -> dict:
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            if case.state not in BLOCK_STATES or case.suspended_state is None:
                raise Denied()
            target = case.suspended_state
            if target == 'INVESTIGATING' and not case.investigator_id:
                raise Denied()
            case.suspended_state = None
            case.state = target
            self._log(case, request_id, 'RESUMED', principal, now, {'target': target})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'RESUME_CASE', request_id, {}, action)

    @staticmethod
    def _conflicted(case: Case, identity: str, *, appellate: bool = False) -> bool:
        if identity in (case.subject_worker_id, case.reporter_id, case.investigator_id,
                        case.second_reviewer_id):
            return True
        if any(ev.author_id == identity for ev in case.evidence.values()):
            return True
        if case.finding is not None and identity == case.finding.author_id:
            return True
        if any(ap.reviewer_id == identity for ap in case.appeals.values()):
            return True
        if appellate and any(ap.appellant_id == identity for ap in case.appeals.values()):
            return True
        return False

    def assign_investigator(self, credential: object, case_id: str, *,
                            investigator_id: str, request_id: str) -> dict:
        _opaque(investigator_id)
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            if case.state not in ('TRIAGED', 'BLOCKED_INDEPENDENCE', 'REOPENED'):
                raise Denied()
            if self._conflicted(case, investigator_id):
                raise Denied()
            # A real credential/specialty/training/lease check is an external AK port.
            if self.eligibility is None:
                raise Unsupported()
            if not self.eligibility.eligible(case_id, investigator_id, 'INVESTIGATOR', now):
                raise Denied()
            self._permit(principal, 'VALIDATE_INVESTIGATOR', case_id, investigator_id, now)
            case.investigator_id = investigator_id
            if case.state == 'BLOCKED_INDEPENDENCE':
                case.state = case.suspended_state or 'TRIAGED'
                case.suspended_state = None
            self._log(case, request_id, 'INVESTIGATOR_ASSIGNED', principal, now,
                      {'assigned_id_digest': digest(investigator_id)})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'ASSIGN_INVESTIGATOR', request_id,
                            {'investigator_id': investigator_id}, action)

    def record_evidence(self, credential: object, case_id: str, *,
                        submission: EvidenceSubmission, request_id: str) -> dict:
        if not isinstance(submission, EvidenceSubmission):
            raise Invalid()
        for item in (submission.evidence_id, submission.source_ref,
                     submission.source_revision, submission.source_event_id,
                     submission.origin_ref, submission.access_scope,
                     submission.author_id):
            _opaque(item)
        for item in (submission.counter_to, submission.corrects):
            if item is not None:
                _opaque(item)
        if submission.counter_to == submission.evidence_id or submission.corrects == submission.evidence_id:
            raise Invalid()
        _hash(submission.content_sha256)
        _hash(submission.manifest_sha256)
        _time(submission.source_occurred_at)
        intent = {'evidence_id': submission.evidence_id, 'content': submission.content_sha256,
                  'manifest': submission.manifest_sha256, 'ciphertext_ref': submission.ciphertext_ref,
                  'source_ref': submission.source_ref, 'revision': submission.source_revision,
                  'source_event': submission.source_event_id,
                  'occurred_at': submission.source_occurred_at,
                  'origin': submission.origin_ref, 'scope': submission.access_scope,
                  'author': submission.author_id, 'counter_to': submission.counter_to,
                  'corrects': submission.corrects}

        def action(case: Case, principal: Principal, now: datetime) -> dict:
            if case.state not in ('INVESTIGATING', 'REOPENED', 'EVIDENCE_READY',
                                  'APPEALED', 'WAITING_EVIDENCE'):
                raise Denied()
            if submission.evidence_id in case.evidence:
                raise Conflict()
            if (submission.counter_to and submission.counter_to not in case.evidence or
                    submission.corrects and submission.corrects not in case.evidence):
                raise Denied()
            # Scoped right is checked on *every* evidence record.
            self._permit(principal, 'WRITE_EVIDENCE', case_id, submission.access_scope, now)
            self._require_seal(submission.ciphertext_ref, submission.manifest_sha256,
                               principal, now)
            if self.source is None:
                raise Unsupported()
            receipt = self.source.verify_source(submission, principal, now)
            if not isinstance(receipt, SourceReceipt):
                raise Denied()
            _time(receipt.valid_until)
            _time(receipt.source_occurred_at)
            expected = (submission.evidence_id, submission.content_sha256,
                        submission.source_ref, submission.source_revision,
                        submission.source_event_id, submission.source_occurred_at,
                        submission.origin_ref, submission.author_id, submission.access_scope)
            actual = (receipt.evidence_id, receipt.content_sha256, receipt.source_ref,
                      receipt.source_revision, receipt.source_event_id,
                      receipt.source_occurred_at, receipt.origin_ref, receipt.author_id,
                      receipt.access_scope)
            if (expected != actual or now >= receipt.valid_until or
                    receipt.classification not in CLASSES or
                    receipt.classification == 'INVALID_UNREADABLE' or
                    not receipt.verifier_id):
                raise Denied()
            ev = Evidence(
                evidence_id=submission.evidence_id,
                content_sha256=submission.content_sha256,
                manifest_sha256=submission.manifest_sha256,
                ciphertext_ref=submission.ciphertext_ref,
                source_ref=submission.source_ref,
                source_revision=submission.source_revision,
                source_event_id=submission.source_event_id,
                source_occurred_at=submission.source_occurred_at,
                received_at=now, origin_ref=submission.origin_ref,
                verifier_id=receipt.verifier_id, author_id=submission.author_id,
                access_scope=submission.access_scope,
                classification=receipt.classification,
                counter_to=submission.counter_to, corrects=submission.corrects,
            )
            # Original entries are never altered. Corrections/counterclaims are new refs.
            case.evidence[ev.evidence_id] = ev
            self._log(case, request_id, 'EVIDENCE_ADMITTED', principal, now,
                      {'content': ev.content_sha256, 'class': ev.classification,
                       'evidence_id': ev.evidence_id})
            self._log(case, request_id + ':custody', 'RECEIVED', principal, now,
                      {'evidence_id': ev.evidence_id, 'manifest': ev.manifest_sha256,
                       'content': ev.content_sha256, 'verifier': ev.verifier_id,
                       'scope': ev.access_scope, 'source_occurred_at': ev.source_occurred_at,
                       'source_ref': ev.source_ref, 'source_revision': ev.source_revision,
                       'source_event_id': ev.source_event_id, 'origin_ref': ev.origin_ref,
                       'author_id': ev.author_id, 'counter_to': ev.counter_to,
                       'corrects': ev.corrects, 'received_at': ev.received_at,
                       'classification': ev.classification},
                      custody=True)
            return _safe_status(case)
        return self._mutate(case_id, credential, 'RECORD_EVIDENCE', request_id,
                            intent, action, submission.access_scope)

    def record_custody(self, credential: object, case_id: str, *,
                       evidence_id: str, operation: str, target_principal: str | None,
                       request_id: str) -> dict:
        _opaque(evidence_id)
        if operation not in ('ACCESS', 'TRANSFER', 'RESEAL', 'REDACTION_REVIEW'):
            raise Invalid()
        if target_principal is not None:
            _opaque(target_principal)
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            ev = case.evidence.get(evidence_id)
            if ev is None:
                raise Denied()
            self._permit(principal, 'CUSTODY_' + operation, case_id,
                         ev.access_scope, now)
            # A custody record is NOT authorization to decrypt or transfer bytes.
            self._log(case, request_id, operation, principal, now,
                      {'evidence_id': evidence_id,
                       'target_digest': digest(target_principal) if target_principal else None},
                      custody=True)
            return _safe_status(case)
        return self._mutate(case_id, credential, 'RECORD_CUSTODY', request_id,
                            {'evidence_id': evidence_id, 'operation': operation,
                             'target_principal': target_principal}, action)

    @staticmethod
    def _sufficiency(case: Case, fact_refs: tuple[str, ...]) -> bool:
        evidence = [case.evidence[ref] for ref in fact_refs]
        corrected = {ev.corrects for ev in case.evidence.values() if ev.corrects}
        if any(ev.evidence_id in corrected for ev in evidence):
            return False
        contradicted = {ev.counter_to for ev in case.evidence.values() if ev.counter_to}
        if any(ev.evidence_id in contradicted for ev in evidence):
            return False
        if any(ev.classification == 'CORROBORATED' for ev in evidence):
            return True
        return len({ev.origin_ref for ev in evidence if ev.classification == 'OBSERVED'}) >= 2

    def propose_finding(self, credential: object, case_id: str, *,
                        conclusion: str, policy_clause: str, policy_revision: int,
                        fact_refs: tuple[str, ...], inference_refs: tuple[str, ...],
                        allegation_refs: tuple[str, ...], counter_refs: tuple[str, ...],
                        sealed_reason_sha256: str, request_id: str) -> dict:
        if conclusion not in ('SUPPORTED', 'NOT_SUPPORTED'):
            raise Invalid()
        _opaque(policy_clause)
        _hash(sealed_reason_sha256)
        if type(policy_revision) is not int or policy_revision < 1:
            raise Invalid()
        refs = (fact_refs, inference_refs, allegation_refs, counter_refs)
        if any(not isinstance(group, tuple) for group in refs):
            raise Invalid()
        if any(not isinstance(ref, str) for group in refs for ref in group):
            raise Invalid()
        if len(set(ref for group in refs for ref in group)) != sum(map(len, refs)):
            raise Invalid()
        intent = dict(conclusion=conclusion, policy_clause=policy_clause,
                      policy_revision=policy_revision, refs=refs,
                      reason_digest=sealed_reason_sha256)
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            if case.state != 'EVIDENCE_READY' or principal.identity != case.investigator_id:
                raise Denied()
            if case.finding is not None or policy_revision != case.policy_revision:
                raise Denied()
            for group in refs:
                if any(ref not in case.evidence for ref in group):
                    raise Denied()
            if any(case.evidence[ref].classification not in ('OBSERVED', 'CORROBORATED')
                   for ref in fact_refs):
                raise Denied()
            if any(case.evidence[ref].classification != 'INFERRED' for ref in inference_refs):
                raise Denied()
            if any(case.evidence[ref].classification != 'CLAIMED' for ref in allegation_refs):
                raise Denied()
            # Explicit counterevidence must never be silently ignored.
            all_counter = {ev.evidence_id for ev in case.evidence.values() if ev.counter_to}
            if not all_counter.issubset(set(counter_refs)):
                raise Denied()
            sufficient = self._sufficiency(case, fact_refs)
            if conclusion == 'SUPPORTED' and not sufficient:
                raise Denied()
            case.finding = Finding(
                conclusion, policy_clause, policy_revision, fact_refs, inference_refs,
                allegation_refs, counter_refs, sealed_reason_sha256,
                principal.identity, 'SUFFICIENT' if sufficient else 'INSUFFICIENT')
            self._log(case, request_id, 'FINDING_PROPOSED', principal, now,
                      {'conclusion': conclusion, 'sufficiency': case.finding.sufficiency,
                       'policy_revision': policy_revision,
                       'reason_digest': sealed_reason_sha256,
                       'finding_digest': digest(case.finding.__dict__)})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'PROPOSE_FINDING', request_id, intent, action)

    def second_review(self, credential: object, case_id: str, *,
                      accepted: bool, request_id: str) -> dict:
        if type(accepted) is not bool:
            raise Invalid()
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            if case.state != 'FINDINGS_DRAFT' or case.finding is None:
                raise Denied()
            if self._conflicted(case, principal.identity):
                raise Denied()
            if not accepted:
                case.suspended_state = case.state
                case.state = 'BLOCKED_INDEPENDENCE'
                self._log(case, request_id, 'INDEPENDENT_REVIEW_REJECTED', principal, now, {})
            else:
                case.second_reviewer_id = principal.identity
                self._log(case, request_id, 'INDEPENDENT_REVIEW_ACCEPTED', principal, now,
                          {'finding_digest': digest(case.finding.__dict__)})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'SECOND_REVIEW', request_id,
                            {'accepted': accepted}, action)

    def owner_disposition(self, credential: object, case_id: str, *,
                          decision_ref: str, disposition: str, request_id: str) -> dict:
        _opaque(decision_ref)
        if disposition not in ('NO_ACTION', 'REVIEW_ONLY', 'REFER_TO_GATEWAY'):
            raise Invalid()
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            if case.state != 'OWNER_DISPOSITION' or case.owner_decision_id:
                raise Denied()
            if self.owner is None:
                raise Unsupported()
            chain = case.events[-1].record_sha256
            if not self.owner.verify_decision(
                    decision_ref=decision_ref, case_id=case_id,
                    case_chain_sha256=chain, action=disposition,
                    policy_revision=case.policy_revision, now=now):
                raise Denied()
            case.owner_decision_id = decision_ref
            # There is no enforcement side effect; even REFER only records a referral.
            self._log(case, request_id, 'OWNER_DECISION_RECORDED', principal, now,
                      {'disposition': disposition, 'decision_ref_digest': digest(decision_ref)})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'OWNER_DISPOSITION', request_id,
                            {'decision_ref': decision_ref, 'disposition': disposition}, action)

    def dismiss_no_evidence(self, credential: object, case_id: str, *,
                            policy_clause: str, request_id: str) -> dict:
        _opaque(policy_clause)
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            if case.state not in ('TRIAGED', 'INVESTIGATING', 'WAITING_EVIDENCE'):
                raise Denied()
            if any(ev.classification in ('OBSERVED', 'CORROBORATED')
                   for ev in case.evidence.values()):
                raise Denied()
            case.state = 'DISMISSED_NO_EVIDENCE'
            self._log(case, request_id, 'NOT_SUPPORTED', principal, now,
                      {'policy_clause': policy_clause, 'reason': 'INSUFFICIENT_EVIDENCE'})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'DISMISS', request_id,
                            {'policy_clause': policy_clause}, action)

    def appeal(self, credential: object, case_id: str, *, appeal_id: str,
               new_evidence_refs: tuple[str, ...], sealed_ground_sha256: str,
               request_id: str) -> dict:
        _opaque(appeal_id)
        _hash(sealed_ground_sha256)
        if not isinstance(new_evidence_refs, tuple) or len(set(new_evidence_refs)) != len(new_evidence_refs):
            raise Invalid()
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            if case.state not in ('CLOSED', 'DISMISSED_NO_EVIDENCE'):
                raise Denied()
            if case.pending_appeal_id is not None or appeal_id in case.appeals:
                raise Conflict()
            if any(ref not in case.evidence for ref in new_evidence_refs):
                raise Denied()
            if principal.identity not in (case.reporter_id, case.subject_worker_id):
                self._permit(principal, 'APPEAL_DELEGATE', case_id, None, now)
            case.appeals[appeal_id] = Appeal(
                appeal_id, principal.identity, new_evidence_refs,
                sealed_ground_sha256, now, case.state)
            case.pending_appeal_id = appeal_id
            case.state = 'APPEALED'
            self._log(case, request_id, 'APPEAL_FILED', principal, now,
                      {'appeal_digest': digest(appeal_id), 'ground': sealed_ground_sha256})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'APPEAL', request_id,
                            {'appeal_id': appeal_id, 'refs': new_evidence_refs,
                             'ground': sealed_ground_sha256}, action)

    def add_appeal_evidence(self, credential: object, case_id: str, *,
                            appeal_id: str, evidence_id: str, request_id: str) -> dict:
        """Link newly sealed counterevidence after filing; prior appeal events persist."""
        _opaque(appeal_id)
        _opaque(evidence_id)
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            ap = case.appeals.get(appeal_id)
            if (case.state != 'APPEALED' or case.pending_appeal_id != appeal_id
                    or ap is None or ap.outcome is not None or evidence_id not in case.evidence):
                raise Denied()
            if principal.identity != ap.appellant_id:
                self._permit(principal, 'APPEAL_DELEGATE', case_id, None, now)
            if evidence_id in ap.new_evidence_refs:
                raise Conflict()
            case.appeals[appeal_id] = replace(
                ap, new_evidence_refs=ap.new_evidence_refs + (evidence_id,))
            self._log(case, request_id, 'APPEAL_EVIDENCE_ADDED', principal, now,
                      {'appeal_digest': digest(appeal_id), 'evidence_digest': digest(evidence_id)})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'ADD_APPEAL_EVIDENCE', request_id,
                            {'appeal_id': appeal_id, 'evidence_id': evidence_id}, action)

    def assign_appeal_reviewer(self, credential: object, case_id: str, *,
                               appeal_id: str, reviewer_id: str, request_id: str) -> dict:
        _opaque(reviewer_id)
        _opaque(appeal_id)
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            ap = case.appeals.get(appeal_id)
            if case.state != 'APPEALED' or ap is None or ap.reviewer_id is not None:
                raise Denied()
            if self._conflicted(case, reviewer_id, appellate=True):
                raise Denied()
            if self.eligibility is None:
                raise Unsupported()
            if not self.eligibility.eligible(case_id, reviewer_id, 'APPEAL_REVIEWER', now):
                raise Denied()
            self._permit(principal, 'VALIDATE_APPEAL_REVIEWER', case_id, reviewer_id, now)
            case.appeals[appeal_id] = replace(ap, reviewer_id=reviewer_id)
            self._log(case, request_id, 'APPEAL_REVIEWER_BOUND', principal, now,
                      {'appeal_digest': digest(appeal_id),
                       'reviewer_digest': digest(reviewer_id)})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'ASSIGN_APPEAL', request_id,
                            {'appeal_id': appeal_id, 'reviewer_id': reviewer_id}, action)

    def resolve_appeal(self, credential: object, case_id: str, *, appeal_id: str,
                       outcome: str, policy_revision: int, request_id: str) -> dict:
        if outcome not in APPEAL_OUTCOMES:
            raise Invalid()
        if type(policy_revision) is not int or policy_revision < 1:
            raise Invalid()
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            ap = case.appeals.get(appeal_id)
            if (case.state != 'APPEALED' or case.pending_appeal_id != appeal_id
                    or ap is None or ap.reviewer_id != principal.identity
                    or ap.outcome is not None):
                raise Denied()
            case.appeals[appeal_id] = replace(ap, outcome=outcome,
                                               policy_revision=policy_revision)
            if outcome in ('AMEND', 'OVERTURN', 'NEEDS_OWNER'):
                case.state = 'WAITING_OWNER'
            elif outcome == 'CONFIRM':
                case.state = ap.original_state
                case.pending_appeal_id = None
            else:
                case.state = 'REOPENED'
                case.pending_appeal_id = None
                case.finding = None
                case.second_reviewer_id = None
                case.owner_decision_id = None
                case.investigator_id = None
                case.policy_revision = policy_revision
            self._log(case, request_id, 'APPEAL_RESOLVED', principal, now,
                      {'appeal_digest': digest(appeal_id), 'outcome': outcome,
                       'policy_revision': policy_revision})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'RESOLVE_APPEAL', request_id,
                            {'appeal_id': appeal_id, 'outcome': outcome,
                             'policy_revision': policy_revision}, action)

    def ratify_appeal(self, credential: object, case_id: str, *, appeal_id: str,
                      decision_ref: str, request_id: str) -> dict:
        _opaque(decision_ref)
        def action(case: Case, principal: Principal, now: datetime) -> dict:
            ap = case.appeals.get(appeal_id)
            if (case.state != 'WAITING_OWNER' or case.pending_appeal_id != appeal_id
                    or ap is None or ap.outcome not in ('AMEND', 'OVERTURN', 'NEEDS_OWNER')):
                raise Denied()
            if self.owner is None:
                raise Unsupported()
            if not self.owner.verify_decision(
                    decision_ref=decision_ref, case_id=case_id,
                    case_chain_sha256=case.events[-1].record_sha256,
                    action='APPEAL_' + ap.outcome,
                    policy_revision=ap.policy_revision, now=now):
                raise Denied()
            case.appeals[appeal_id] = replace(ap, ratified=True)
            case.pending_appeal_id = None
            case.owner_decision_id = None
            case.policy_revision = ap.policy_revision
            case.state = 'REOPENED' if ap.outcome in ('AMEND', 'OVERTURN') else ap.original_state
            if case.state == 'REOPENED':
                case.finding = None
                case.investigator_id = None
                case.second_reviewer_id = None
            self._log(case, request_id, 'APPEAL_RATIFIED', principal, now,
                      {'appeal_digest': digest(appeal_id), 'outcome': ap.outcome,
                       'decision_ref_digest': digest(decision_ref)})
            return _safe_status(case)
        return self._mutate(case_id, credential, 'RATIFY_APPEAL', request_id,
                            {'appeal_id': appeal_id, 'decision_ref': decision_ref}, action)

    def audit_integrity(self, credential: object, case_id: str) -> dict:
        principal, now = self._actor(credential)
        case = self.store.read(case_id)
        self._permit(principal, 'AUDIT_CASE', case_id, None, now)
        assert_chain(case.events)
        assert_chain(case.custody)
        # Audit can attest the *local metadata hash-chain* only. Actual blob bytes,
        # encrypted remote storage and signed archive verification require P2 ports.
        return {'event_chain': 'LOCALLY_CONSISTENT',
                'custody_chain': 'LOCALLY_CONSISTENT',
                'external_blob_integrity': 'NOT_VERIFIED',
                'external_authority': 'NOT_VERIFIED'}

    def request_signed_notice(self, *_args: object, **_kwargs: object) -> None:
        """Existing signed-enforcement.v1 trusted signer is NOT available in P1.

        This API cannot mint signed notices or turn findings into enforcement.
        """
        raise Unsupported()


def build_component(deps: Mapping[str, object]) -> InvestigationService:
    """Provisional Oversight equivalent of build_component(deps); no side effects."""
    return InvestigationService(deps)
