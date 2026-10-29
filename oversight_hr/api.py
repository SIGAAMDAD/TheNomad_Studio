"""S109-S111: independent, project-neutral HR governance and case authority.

All external authority is injected. This module NEVER signs a notice, decrypts
case text, updates the Studio roster, or silently invokes provider services.
Only metadata and sealed references are journaled. Real private integration,
case confidentiality and canonical notice contract remain P2/P4 gates.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import re
import secrets
import time
from typing import Any, Callable, Mapping

from .models import (ALLOWED_ACTIONS, CASE_STATES, EVIDENCE_CLASSES, TRANSITIONS,
                     EvidenceRef, Result, SealedReport, Status)
from .storage import Conflict, Journal, MemoryJournal, digest

_SHA = re.compile(r'^[0-9a-f]{64}$')
_ALLOWED_ROUTES = frozenset({'WORK_DISPUTE', 'CONDUCT', 'SECURITY', 'PERFORMANCE'})
_ALLOWED_WORKFORCE = frozenset({'JOINER', 'MOVER', 'LEAVER', 'REINSTATE', 'RESTRICT'})
_ALLOWED_RECOMMENDATIONS = frozenset({'QUALIFY', 'COACH', 'RETEST', 'REASSIGN',
    'REVIEW_PERSONA', 'REQUEST_INDEPENDENT_REVIEW', 'REQUEST_TEMPORARY_PAUSE',
    'REINSTATE', 'RETIRE', 'NO_ACTION'})
_ALLOWED_REASONS = frozenset({'CAPABILITY', 'CONDUCT', 'SECURITY', 'QUALIFICATION',
                               'OPERATIONAL', 'INSUFFICIENT_EVIDENCE'})
_ALLOWED_PERSONA_AXES = frozenset({'verbosity', 'challenge_style', 'directness',
                                    'collaboration_style', 'response_structure'})


def _ok_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def _deny(code: str, status: Status = Status.DENIED) -> Result:
    return Result(status, code)


class HRComponent:
    """Dependency-injected facade. Never implicitly creates confidential services.

    Port names: journal, access, owner, custody, evidence, independence,
    signed_notice, assignment, enforcement, archive, clock. The caller MUST
    authenticate identities independently; a passed principal string is not proof.
    """

    def __init__(self, deps: Mapping[str, object]):
        self._journal: Journal = deps.get('journal') or MemoryJournal()
        self._access = deps.get('access')
        self._owner = deps.get('owner')
        self._custody = deps.get('custody')
        self._evidence = deps.get('evidence')
        self._independence = deps.get('independence')
        self._signed_notice = deps.get('signed_notice')
        self._assignment = deps.get('assignment')
        self._enforcement = deps.get('enforcement')
        self._archive = deps.get('archive')
        self._clock: Callable[[], float] = deps.get('clock') or time.time

    @staticmethod
    def _port(port: object | None, operation: str, *args: object) -> Any:
        # A disconnected, throwing or unhealthy provider is never authority.
        if port is None:
            return None
        try:
            return getattr(port, operation)(*args)
        except Exception:
            return None

    def _permitted(self, principal: str, action: str, case_id: str | None = None) -> bool:
        return bool(principal and self._port(self._access, 'permits',
                                             principal, action, case_id))

    def _owner_ok(self, decision: str | None, action: str,
                  subject: str, policy: int) -> bool:
        return bool(decision and self._port(self._owner, 'verify',
                                             decision, action, subject, policy))

    def _cached(self, event_id: str, input_data: Any) -> tuple[Result | None, str]:
        fingerprint = digest(input_data)
        r = self._journal.receipt(event_id, fingerprint)
        if r:
            return Result(Status.DUPLICATE, 'IDEMPOTENT_REPLAY', r.value), fingerprint
        return None, fingerprint

    def _commit(self, namespace: str, key: str, revision: int,
                record: dict[str, Any], event_id: str, fingerprint: str,
                response: dict[str, Any] | None = None) -> Result:
        payload = response or {}
        try:
            if not self._journal.commit(namespace, key, revision, record, event_id,
                                        fingerprint, payload):
                stored = self._journal.receipt(event_id, fingerprint)
                return Result(Status.DUPLICATE, 'IDEMPOTENT_REPLAY', stored.value)
            return Result(Status.OK, 'COMMITTED_LOCAL_METADATA', payload)
        except Conflict:
            return _deny('REVISION_OR_EVENT_CONFLICT', Status.CONFLICT)

    def _policy(self) -> tuple[int, dict[str, Any] | None]:
        return self._journal.get('policy', 'current')

    def set_policy(self, principal: str, event_id: str, expected_revision: int,
                   policy_digest: str, owner_decision_ref: str) -> Result:
        """Owner-verified, CAS-protected policy revision; immutable audit history."""
        if not self._permitted(principal, 'policy_write'):
            return _deny('POLICY_WRITE_UNAUTHORIZED')
        if not event_id or expected_revision < 0 or not _ok_sha(policy_digest):
            return _deny('INVALID_POLICY_REQUEST')
        try:
            old, fingerprint = self._cached(event_id,
                (expected_revision, policy_digest, owner_decision_ref))
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if old:
            return old
        revision, old_policy = self._policy()
        if expected_revision != revision:
            return _deny('STALE_POLICY_REVISION', Status.CONFLICT)
        if not self._owner_ok(owner_decision_ref, 'SET_HR_POLICY', 'hr_policy', revision + 1):
            return _deny('OWNER_VERIFICATION_REQUIRED', Status.OWNER_PENDING)
        record = {'revision': revision + 1, 'digest': policy_digest,
                  'previous_digest': old_policy['digest'] if old_policy else None,
                  'owner_decision_ref': owner_decision_ref,
                  'history': (old_policy.get('history', []) + [
                      {k: v for k, v in old_policy.items() if k != 'history'}]
                      if old_policy else [])}
        return self._commit('policy', 'current', revision, record, event_id, fingerprint,
                            {'policy_revision': revision + 1, 'policy_digest': policy_digest})

    def request_workforce(self, principal: str, request_id: str, worker_id: str,
                          operation: str, target_role: str, target_department: str,
                          expected_assignment_epoch: int, qualification_digest: str) -> Result:
        """Create a durable recommendation request; NEVER enroll or retire a worker."""
        if not self._permitted(principal, 'workforce_request'):
            return _deny('WORKFORCE_REQUEST_UNAUTHORIZED')
        if (not all((request_id, worker_id, target_role, target_department)) or
            operation not in _ALLOWED_WORKFORCE or expected_assignment_epoch < 0 or
            not _ok_sha(qualification_digest)):
            return _deny('INVALID_WORKFORCE_REQUEST')
        version, policy = self._policy()
        if not policy:
            return _deny('POLICY_MISSING', Status.OWNER_PENDING)
        inp = (worker_id, operation, target_role, target_department,
               expected_assignment_epoch, qualification_digest)
        try:
            prev, fp = self._cached('workforce:' + request_id, inp)
        except Conflict:
            return _deny('REQUEST_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        record = {'request_id': request_id, 'worker_id': worker_id,
                  'operation': operation, 'target_role': target_role,
                  'target_department': target_department, 'expected_assignment_epoch': expected_assignment_epoch,
                  'qualification_digest': qualification_digest, 'policy_revision': version,
                  'state': 'PENDING_OWNER', 'owner_decision_ref': None,
                  'studio_effect': 'NOT_REQUESTED'}
        return self._commit('workforce', request_id, 0, record, 'workforce:' + request_id,
                            fp, {'request_id': request_id, 'state': 'PENDING_OWNER'})

    def approve_workforce(self, principal: str, event_id: str, request_id: str,
                          owner_decision_ref: str, current_assignment_epoch: int) -> Result:
        if not self._permitted(principal, 'workforce_approve'):
            return _deny('WORKFORCE_APPROVAL_UNAUTHORIZED')
        inp = (request_id, owner_decision_ref, current_assignment_epoch)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        rev, record = self._journal.get('workforce', request_id)
        if not record or record['state'] != 'PENDING_OWNER':
            return _deny('UNKNOWN_OR_CLOSED_WORKFORCE_REQUEST')
        if record['expected_assignment_epoch'] != current_assignment_epoch:
            return _deny('STALE_ASSIGNMENT_EPOCH')
        if not self._owner_ok(owner_decision_ref, 'APPROVE_WORKFORCE_' + record['operation'],
                              record['worker_id'], record['policy_revision']):
            return _deny('OWNER_VERIFICATION_REQUIRED', Status.OWNER_PENDING)
        record['state'] = 'OWNER_APPROVED_STUDIO_PENDING'
        record['owner_decision_ref'] = owner_decision_ref
        return self._commit('workforce', request_id, rev, record, event_id, fp,
                            {'request_id': request_id, 'state': record['state'],
                             'studio_effect': 'NOT_APPLIED'})

    def workforce_status(self, principal: str, request_id: str) -> Result:
        if not self._permitted(principal, 'workforce_read'):
            return _deny('WORKFORCE_READ_UNAUTHORIZED')
        _, rec = self._journal.get('workforce', request_id)
        if rec is None:
            return _deny('NOT_FOUND')
        return Result(Status.OK, 'HR_REQUEST_ONLY', {'request_id': request_id,
            'worker_id': rec['worker_id'], 'state': rec['state'],
            'studio_effect': 'NOT_APPLIED', 'policy_revision': rec['policy_revision']})

    def intake_sealed(self, principal: str, report: SealedReport) -> Result:
        """Duplicate-safe encrypted intake; accepts NO case plaintext parameter."""
        if not self._permitted(principal, 'case_intake'):
            return _deny('INTAKE_UNAUTHORIZED')
        if (not report.event_id or not report.reporter_id or not report.source_ref or
            not report.ciphertext_ref or not _ok_sha(report.ciphertext_sha256) or
            not report.occurred_at or report.summary_class != 'RESTRICTED'):
            return _deny('INVALID_SEALED_REPORT')
        inp = asdict(report)
        try:
            prev, fp = self._cached('intake:' + report.event_id, inp)
        except Conflict:
            return _deny('INTAKE_DIGEST_CONFLICT', Status.CONFLICT)
        if prev:
            return prev
        if self._custody is None:
            return _deny('NO_CONFIDENTIAL_CUSTODY', Status.BLOCKED_PRIVATE_ACCESS)
        if not self._port(self._custody, 'check_sealed', report.ciphertext_ref, report.ciphertext_sha256,
                                          report.reporter_id):
            return _deny('SEALED_CUSTODY_NOT_VERIFIED', Status.BLOCKED_PRIVATE_ACCESS)
        case_id = 'CASE-' + secrets.token_hex(16)
        record = {'case_id': case_id, 'state': 'SEALED_RECEIVED',
                  'reporter': report.reporter_id, 'subject': report.subject_worker_id,
                  'ciphertext_ref': report.ciphertext_ref,
                  'custody_sha256': report.ciphertext_sha256,
                  'source_ref': report.source_ref, 'occurred_at': report.occurred_at,
                  'incident_category': report.incident_category,
                  'route': None, 'investigator': None, 'reviewer': None,
                  'implicated': [], 'evidence': [], 'finding': None,
                  'finding_versions': [], 'appeals': [], 'disposition': None}
        return self._commit('cases', case_id, 0, record,
                            'intake:' + report.event_id, fp,
                            {'case_id': case_id, 'state': 'SEALED_RECEIVED'})

    def _read_case(self, principal: str, case_id: str, action: str) -> tuple[int, dict[str, Any] | None]:
        if not self._permitted(principal, action, case_id):
            return -1, None
        # A broad HR role alone is insufficient: custody grants remain case-scoped.
        if self._custody is None or not self._port(self._custody, 'scoped_access', case_id, principal):
            return -1, None
        return self._journal.get('cases', case_id)

    def case_status(self, principal: str, case_id: str) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_read')
        if rev == -1:
            return _deny('CASE_ACCESS_DENIED')
        if rec is None:
            return _deny('CASE_NOT_FOUND')
        # Explicit allowlist: no source refs, reporter, witness or finding text.
        return Result(Status.OK, 'CASE_METADATA_ONLY', {
            'case_id': case_id, 'state': rec['state'], 'revision': rev,
            'route': rec['route'], 'evidence_count': len(rec['evidence']),
            'appeal_count': len(rec['appeals'])})

    def public_status(self) -> Result:
        """Never exposes whether a particular private case exists."""
        return Result(Status.OK, 'PRIVATE_CASE_DATA_WITHHELD',
                      {'integration': 'PRIVATE_REPO_INTEGRATION_NOT_VERIFIED',
                       'private_access': 'NOT_CONNECTED'})

    def audit_case(self, principal: str, case_id: str) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_audit')
        if rev == -1:
            return _deny('CASE_AUDIT_UNAUTHORIZED')
        if rec is None:
            return _deny('CASE_NOT_FOUND')
        return Result(Status.OK, 'HASHED_AUDIT_ONLY',
                      {'events': list(self._journal.audit('cases', case_id))})

    def triage(self, principal: str, case_id: str, event_id: str, claimed_route: str,
               verified_conduct_trigger: EvidenceRef | None = None) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_triage')
        if rev == -1:
            return _deny('TRIAGE_UNAUTHORIZED')
        inp = (case_id, claimed_route, asdict(verified_conduct_trigger) if verified_conduct_trigger else None)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        if rec is None or rec['state'] != 'SCREENED':
            return _deny('CASE_NOT_SCREENED')
        if claimed_route not in _ALLOWED_ROUTES:
            return _deny('INVALID_ROUTE')
        verified = False
        if claimed_route in {'CONDUCT', 'SECURITY'} and verified_conduct_trigger is not None:
            verified = self._verified_evidence(verified_conduct_trigger)
        # A personality disagreement never upgrades to a conduct finding.
        route = claimed_route if claimed_route not in {'CONDUCT', 'SECURITY'} or verified else 'WORK_DISPUTE'
        rec['route'] = route
        rec['state'] = 'TRIAGED'
        return self._commit('cases', case_id, rev, rec, event_id, fp,
                            {'state': 'TRIAGED', 'route': route})

    def _verified_evidence(self, ev: EvidenceRef) -> bool:
        return bool(ev.evidence_class in {'OBSERVED', 'CORROBORATED'} and
                    _ok_sha(ev.byte_sha256) and ev.source_ref and ev.source_revision and
                    ev.collector_id and self._evidence is not None and
                    self._port(self._evidence, 'verify', ev.source_ref, ev.byte_sha256,
                                          ev.source_revision, ev.collector_id))

    def record_evidence(self, principal: str, case_id: str, evidence: EvidenceRef) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_evidence')
        if rev == -1:
            return _deny('EVIDENCE_UNAUTHORIZED')
        inp = (case_id, asdict(evidence))
        try:
            prev, fp = self._cached(evidence.event_id, inp)
        except Conflict:
            return _deny('EVIDENCE_EVENT_CONFLICT', Status.CONFLICT)
        if prev:
            return prev
        if rec is None or rec['state'] not in {'INVESTIGATING', 'WAITING_EVIDENCE', 'EVIDENCE_READY'}:
            return _deny('CASE_NOT_INVESTIGATING')
        if rec['investigator'] != principal or not self._custody or not self._port(self._custody, 'scoped_access', case_id, principal):
            return _deny('CUSTODY_ACCESS_REQUIRED', Status.BLOCKED_PRIVATE_ACCESS)
        if evidence.evidence_class not in EVIDENCE_CLASSES or not evidence.event_id or not _ok_sha(evidence.byte_sha256):
            return _deny('INVALID_EVIDENCE')
        # Claims, inferences and disputes are preserved as such; never marked verified.
        verified = self._verified_evidence(evidence)
        if evidence.evidence_class in {'OBSERVED', 'CORROBORATED'} and not verified:
            return _deny('SOURCE_EVIDENCE_NOT_VERIFIED', Status.WAITING_EVIDENCE)
        rec['evidence'].append({'source_ref': evidence.source_ref,
            'source_revision': evidence.source_revision,
            'byte_sha256': evidence.byte_sha256,
            'class': evidence.evidence_class, 'verified': verified})
        return self._commit('cases', case_id, rev, rec, evidence.event_id, fp,
                            {'evidence_class': evidence.evidence_class, 'verified': verified})

    def assign_investigator(self, principal: str, case_id: str, event_id: str,
                            candidate: str, implicated: tuple[str, ...] = ()) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_assign')
        if rev == -1:
            return _deny('ASSIGN_UNAUTHORIZED')
        inp = (case_id, candidate, implicated)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        if rec is None or rec['state'] not in {'TRIAGED', 'BLOCKED_INDEPENDENCE', 'REOPENED'}:
            return _deny('ASSIGN_INVALID_STATE')
        if (not candidate or candidate in {rec['subject'], rec['reporter'], principal} or
            candidate in implicated or
            self._independence is None or not self._port(self._independence, 'eligible', 
                candidate, case_id, rec['subject'], rec['reporter'], implicated, 'INVESTIGATE')):
            return _deny('INVESTIGATOR_CONFLICT', Status.BLOCKED_INDEPENDENCE)
        if self._custody is None or not self._port(self._custody, 'scoped_access', case_id, candidate):
            return _deny('INVESTIGATOR_NO_CASE_CUSTODY', Status.BLOCKED_PRIVATE_ACCESS)
        rec['investigator'] = candidate
        rec['implicated'] = list(implicated)
        # Assigning is not equivalent to starting investigation.
        return self._commit('cases', case_id, rev, rec, event_id, fp,
                            {'investigator_assigned': True, 'state': rec['state']})

    def assign_reviewer(self, principal: str, case_id: str, event_id: str,
                        candidate: str) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_assign')
        if rev == -1:
            return _deny('ASSIGN_UNAUTHORIZED')
        inp = (case_id, candidate, 'reviewer')
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        if rec is None or rec['state'] != 'FINDINGS_DRAFT':
            return _deny('REVIEWER_ASSIGN_INVALID_STATE')
        if (not candidate or candidate in {rec['subject'], rec['reporter'],
                                           rec['investigator'], principal} or
            candidate in rec['implicated'] or self._independence is None or
            not self._port(self._independence, 'eligible', candidate, case_id, rec['subject'],
                rec['reporter'], tuple(rec['implicated']), 'SECOND_REVIEW')):
            return _deny('REVIEWER_CONFLICT', Status.BLOCKED_INDEPENDENCE)
        if self._custody is None or not self._port(self._custody, 'scoped_access', case_id, candidate):
            return _deny('REVIEWER_NO_CASE_CUSTODY', Status.BLOCKED_PRIVATE_ACCESS)
        rec['reviewer'] = candidate
        return self._commit('cases', case_id, rev, rec, event_id, fp,
                            {'reviewer_assigned': True})

    def propose_finding(self, principal: str, case_id: str, event_id: str,
                        policy_clause: str, evidence_sha256: str,
                        counter_evidence_digests: tuple[str, ...] = ()) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_findings')
        if rev == -1:
            return _deny('FINDINGS_UNAUTHORIZED')
        inp = (case_id, policy_clause, evidence_sha256, counter_evidence_digests)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        if rec is None or rec['state'] != 'EVIDENCE_READY' or rec['investigator'] != principal:
            return _deny('FINDINGS_INVALID_STATE_OR_AUTHOR')
        if (not policy_clause or not _ok_sha(evidence_sha256) or
            any(not _ok_sha(x) for x in counter_evidence_digests)):
            return _deny('INVALID_FINDING_PROVENANCE')
        matching = [e for e in rec['evidence'] if e['byte_sha256'] == evidence_sha256 and
                    e['class'] == 'CORROBORATED' and e['verified']]
        if not matching:
            return _deny('CORROBORATED_EVIDENCE_REQUIRED', Status.WAITING_EVIDENCE)
        policy_rev, policy = self._policy()
        if not policy:
            return _deny('POLICY_MISSING', Status.OWNER_PENDING)
        rec['finding'] = {'author': principal, 'policy_clause': policy_clause,
            'policy_revision': policy_rev, 'verified_evidence_sha256': evidence_sha256,
            'counter_evidence_digests': list(counter_evidence_digests),
            'review_status': 'DRAFT_ONLY'}
        rec['finding_versions'].append(dict(rec['finding']))
        return self._commit('cases', case_id, rev, rec, event_id, fp,
                            {'finding': 'DRAFT_ONLY', 'policy_revision': policy_rev})

    def record_second_review(self, principal: str, case_id: str, event_id: str,
                             verdict: str, verified_review_digest: str) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_second_review')
        if rev == -1:
            return _deny('SECOND_REVIEW_UNAUTHORIZED')
        inp = (case_id, verdict, verified_review_digest)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        if (not rec or rec['state'] != 'SECOND_REVIEW' or rec['reviewer'] != principal or
            verdict not in {'CONFIRM', 'REJECT', 'MORE_EVIDENCE'} or
            not _ok_sha(verified_review_digest)):
            return _deny('SECOND_REVIEW_INVALID')
        if self._evidence is None or not self._port(self._evidence, 'verify', 
            'artifact://review/' + case_id, verified_review_digest, 'case:'+case_id, principal):
            return _deny('REVIEW_EVIDENCE_UNVERIFIED', Status.WAITING_EVIDENCE)
        rec['finding']['review_status'] = 'ACCEPTED_SECOND_REVIEW' if verdict == 'CONFIRM' else 'SECOND_REVIEW_BLOCKED'
        rec['finding']['second_review_digest'] = verified_review_digest
        rec['finding']['second_review_verdict'] = verdict
        rec['finding_versions'].append(dict(rec['finding']))
        return self._commit('cases', case_id, rev, rec, event_id, fp,
                            {'second_review': verdict, 'enforcement': 'NONE'})

    def resolve_appeal(self, principal: str, case_id: str, event_id: str,
                       appeal_id: str, owner_decision_ref: str) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_appeal_disposition')
        if rev == -1:
            return _deny('APPEAL_DISPOSITION_UNAUTHORIZED')
        inp = (case_id, appeal_id, owner_decision_ref)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        if not rec or rec['state'] != 'APPEALED' or not rec['appeals'] or rec['appeals'][-1]['appeal_id'] != appeal_id:
            return _deny('APPEAL_NOT_PENDING')
        _, policy = self._policy()
        if not policy or not self._owner_ok(owner_decision_ref, 'RESOLVE_APPEAL', case_id, policy['revision']):
            return _deny('OWNER_APPEAL_DECISION_REQUIRED', Status.OWNER_PENDING)
        rec['appeals'][-1]['effect'] = 'OWNER_RATIFIED_RECORD_ONLY'
        rec['appeals'][-1]['owner_decision_ref'] = owner_decision_ref
        rec['state'] = 'CLOSED'
        return self._commit('cases', case_id, rev, rec, event_id, fp,
                            {'state': 'CLOSED', 'appeal_outcome': rec['appeals'][-1]['proposed_outcome'],
                             'studio_effect': 'NOT_APPLIED'})

    def reopen_case(self, principal: str, case_id: str, event_id: str,
                    new_evidence: EvidenceRef, policy_revision: int) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_reopen')
        if rev == -1:
            return _deny('REOPEN_UNAUTHORIZED')
        inp = (case_id, asdict(new_evidence), policy_revision)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        current_policy, policy = self._policy()
        if (rec is None or rec['state'] not in {'CLOSED', 'DISMISSED_NO_EVIDENCE', 'APPEALED'} or
            current_policy != policy_revision or policy is None or not self._verified_evidence(new_evidence)):
            return _deny('NEW_VERIFIED_EVIDENCE_AND_CURRENT_POLICY_REQUIRED', Status.WAITING_EVIDENCE)
        rec['state'] = 'REOPENED'
        rec['investigator'] = None
        rec['reviewer'] = None
        # Preserve prior finding versions, appeal chain and owner disposition.
        rec['finding'] = None
        rec['reopen_evidence_digest'] = new_evidence.byte_sha256
        return self._commit('cases', case_id, rev, rec, event_id, fp,
                            {'state': 'REOPENED', 'new_evidence_digest': new_evidence.byte_sha256})

    def advance_case(self, principal: str, case_id: str, event_id: str,
                     target_state: str, owner_decision_ref: str | None = None) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_transition')
        if rev == -1:
            return _deny('TRANSITION_UNAUTHORIZED')
        inp = (case_id, target_state, owner_decision_ref)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        if rec is None or target_state not in CASE_STATES or target_state not in TRANSITIONS.get(rec['state'], set()):
            return _deny('ILLEGAL_CASE_TRANSITION')
        if target_state in {'REOPENED', 'APPEALED'} or (rec['state'] == 'APPEALED' and target_state == 'CLOSED'):
            return _deny('EXPLICIT_REOPEN_OR_APPEAL_REVIEW_REQUIRED')
        if target_state == 'SCREENED' and (self._custody is None or
            not self._port(self._custody, 'check_sealed', rec['ciphertext_ref'], rec['custody_sha256'], rec['reporter'])):
            return _deny('CASE_KEY_OR_CUSTODY_MISSING', Status.BLOCKED_PRIVATE_ACCESS)
        if target_state == 'TRIAGED' and rec['route'] not in _ALLOWED_ROUTES:
            return _deny('ROUTE_REQUIRED')
        if target_state == 'INVESTIGATING' and (not rec['investigator'] or
            not self._custody or not self._port(self._custody, 'scoped_access', case_id, rec['investigator'])):
            return _deny('INDEPENDENT_INVESTIGATOR_REQUIRED', Status.BLOCKED_INDEPENDENCE)
        if target_state == 'EVIDENCE_READY' and not any(e['verified'] and
                e['class'] in {'OBSERVED', 'CORROBORATED'} for e in rec['evidence']):
            return _deny('VERIFIED_EVIDENCE_REQUIRED', Status.WAITING_EVIDENCE)
        if target_state == 'FINDINGS_DRAFT' and rec['finding'] is None:
            return _deny('FINDINGS_REQUIRED')
        if target_state == 'SECOND_REVIEW' and (not rec['reviewer'] or
            rec['reviewer'] in {rec['subject'], rec['reporter'], rec['investigator']} or
            rec['reviewer'] in rec['implicated']):
            return _deny('INDEPENDENT_SECOND_REVIEW_REQUIRED', Status.BLOCKED_INDEPENDENCE)
        if target_state == 'OWNER_DISPOSITION' and rec['finding'] and rec['finding'].get('review_status') != 'ACCEPTED_SECOND_REVIEW':
            return _deny('SECOND_REVIEW_OUTCOME_REQUIRED')
        if target_state == 'OWNER_DISPOSITION':
            _, policy = self._policy()
            if not policy or not self._owner_ok(owner_decision_ref, 'CASE_DISPOSITION',
                                                case_id, policy['revision']):
                return _deny('OWNER_DISPOSITION_REQUIRED', Status.OWNER_PENDING)
        if target_state == 'CLOSED' and rec['disposition'] is None:
            return _deny('DISPOSITION_MISSING')
        rec['state'] = target_state
        if target_state == 'OWNER_DISPOSITION':
            rec['disposition'] = {'owner_decision_ref': owner_decision_ref,
                                  'status': 'OWNER_VERIFIED_RECORD_ONLY'}
        return self._commit('cases', case_id, rev, rec, event_id, fp,
                            {'state': target_state})

    def appeal(self, principal: str, case_id: str, appeal_id: str,
               reviewer: str, new_evidence_sha256: str,
               requested_outcome: str) -> Result:
        rev, rec = self._read_case(principal, case_id, 'case_appeal')
        if rev == -1:
            return _deny('APPEAL_UNAUTHORIZED')
        inp = (case_id, reviewer, new_evidence_sha256, requested_outcome)
        try:
            prev, fp = self._cached('appeal:' + appeal_id, inp)
        except Conflict:
            return _deny('APPEAL_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        if rec is None or rec['state'] not in {'CLOSED', 'DISMISSED_NO_EVIDENCE', 'APPEALED'}:
            return _deny('APPEAL_INVALID_STATE')
        if (not _ok_sha(new_evidence_sha256) or requested_outcome not in
            {'CONFIRM', 'AMEND', 'OVERTURN', 'INSUFFICIENT_EVIDENCE', 'NEEDS_OWNER'}):
            return _deny('INVALID_APPEAL')
        if (reviewer in {rec['investigator'], rec['reviewer'], rec['subject'], rec['reporter'], principal} or
            reviewer in rec['implicated'] or self._independence is None or
            not self._port(self._independence, 'eligible', reviewer, case_id, rec['subject'],
                rec['reporter'], tuple(rec['implicated']), 'APPEAL')):
            return _deny('APPEAL_INDEPENDENCE_FAILURE', Status.BLOCKED_INDEPENDENCE)
        if not self._custody or not self._port(self._custody, 'scoped_access', case_id, reviewer):
            return _deny('APPEAL_CUSTODY_DENIED', Status.BLOCKED_PRIVATE_ACCESS)
        rec['appeals'].append({'appeal_id': appeal_id, 'reviewer': reviewer,
            'new_evidence_sha256': new_evidence_sha256,
            'proposed_outcome': requested_outcome, 'effect': 'OWNER_PENDING'})
        rec['state'] = 'APPEALED'
        return self._commit('cases', case_id, rev, rec, 'appeal:' + appeal_id, fp,
                            {'appeal_id': appeal_id, 'state': 'APPEALED',
                             'effect': 'OWNER_PENDING'})

    def recommend(self, principal: str, recommendation_id: str, worker_id: str,
                  recommendation: str, reason_class: str, evidence_sha256: str,
                  assignment_epoch: int, case_id: str | None = None,
                  evidence_source_ref: str = '', evidence_source_revision: str = '') -> Result:
        """HR/RECOMMENDATION/1; every recommendation is effect-free by design."""
        if not self._permitted(principal, 'recommend', case_id):
            return _deny('RECOMMENDATION_UNAUTHORIZED')
        if (not recommendation_id or not worker_id or not _ok_sha(evidence_sha256) or
            recommendation not in _ALLOWED_RECOMMENDATIONS or
            reason_class not in _ALLOWED_REASONS or assignment_epoch < 0):
            return _deny('INVALID_RECOMMENDATION')
        if (self._evidence is None or not evidence_source_ref or not evidence_source_revision or
            not self._port(self._evidence, 'verify', evidence_source_ref, evidence_sha256,
                                      evidence_source_revision, principal)):
            return _deny('RECOMMENDATION_EVIDENCE_NOT_VERIFIED', Status.WAITING_EVIDENCE)
        if case_id is not None:
            _, rec = self._read_case(principal, case_id, 'case_read')
            if rec is None or rec['subject'] != worker_id:
                return _deny('CASE_SCOPE_MISMATCH')
        revision, policy = self._policy()
        if policy is None:
            return _deny('POLICY_MISSING', Status.OWNER_PENDING)
        inp = (worker_id, recommendation, reason_class, evidence_sha256,
               assignment_epoch, case_id, revision, evidence_source_ref,
               evidence_source_revision)
        try:
            prev, fp = self._cached('recommend:' + recommendation_id, inp)
        except Conflict:
            return _deny('RECOMMENDATION_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        record = {'version': 'NOMAD/HR/RECOMMENDATION/1',
                  'recommendation_id': recommendation_id, 'subject_worker_id': worker_id,
                  'observed_evidence_digest': evidence_sha256,
                  'recommendation': recommendation, 'reason_class': reason_class,
                  'current_assignment_epoch': assignment_epoch,
                  'policy_revision': revision, 'requires_owner': True,
                  'effect_status': 'OWNER_PENDING', 'case_id': case_id,
                  'owner_decision_ref': None}
        return self._commit('recommendations', recommendation_id, 0, record,
                            'recommend:' + recommendation_id, fp,
                            {'recommendation': dict(record)})

    def review_competency(self, principal: str, worker_id: str, event_id: str,
                          role_class: str, source_period: str, sample_size: int,
                          accepted_artifacts: tuple[tuple[str, str], ...],
                          attributable_rework: tuple[tuple[str, str], ...],
                          assignment_epoch: int) -> Result:
        """Evidence-bound review, no sentiment or productivity scalar scoring.

        Each (immutable ref, sha256) tuple must verify with the evidence port.
        """
        if not self._permitted(principal, 'competency_review'):
            return _deny('COMPETENCY_REVIEW_UNAUTHORIZED')
        if (not worker_id or not role_class or not source_period or sample_size < 3 or
            sample_size != len(accepted_artifacts) + len(attributable_rework) or
            assignment_epoch < 0):
            return _deny('INSUFFICIENT_COMPARABLE_SAMPLE', Status.WAITING_EVIDENCE)
        pairs = accepted_artifacts + attributable_rework
        if not pairs or self._evidence is None or any(
            not _ok_sha(h) or not self._port(self._evidence, 'verify', ref, h, source_period, principal)
            for ref, h in pairs):
            return _deny('COMPETENCY_PROVENANCE_UNVERIFIED', Status.WAITING_EVIDENCE)
        inp = (worker_id, role_class, source_period, sample_size, pairs, assignment_epoch)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        rec = {'worker_id': worker_id, 'role_class': role_class,
               'source_period': source_period, 'sample_size': sample_size,
               'accepted_evidence': len(accepted_artifacts),
               'attributable_rework_evidence': len(attributable_rework),
               'evidence_digest': digest(pairs), 'assignment_epoch': assignment_epoch,
               'possible_action': 'COACH' if attributable_rework else 'NO_ACTION',
               'effect': 'RECOMMENDATION_ONLY'}
        return self._commit('competency', event_id, 0, rec, event_id, fp,
                            {'review': rec})

    def propose_persona_revision(self, principal: str, event_id: str, worker_id: str,
                                 current_revision: int, current_digest: str,
                                 proposed_axis: str, proposed_digest: str,
                                 evidence_digest: str, independent_reviewer: str,
                                 evidence_source_ref: str = '',
                                 evidence_source_revision: str = '') -> Result:
        if not self._permitted(principal, 'persona_recommend'):
            return _deny('PERSONA_REVIEW_UNAUTHORIZED')
        if (not worker_id or current_revision < 1 or proposed_axis not in _ALLOWED_PERSONA_AXES or
            not all(_ok_sha(x) for x in (current_digest, proposed_digest, evidence_digest)) or
            not independent_reviewer or independent_reviewer in {worker_id, principal} or
            self._independence is None or not self._port(self._independence, 'eligible', 
                independent_reviewer, 'PERSONA', worker_id, principal, (), 'PERSONA_REVIEW')):
            return _deny('UNBOUNDED_OR_UNINDEPENDENT_PERSONA_REQUEST')
        if (self._evidence is None or not evidence_source_ref or not evidence_source_revision or
            not self._port(self._evidence, 'verify', evidence_source_ref, evidence_digest,
                                      evidence_source_revision, principal)):
            return _deny('PERSONA_SOURCE_UNVERIFIED', Status.WAITING_EVIDENCE)
        inp = (worker_id, current_revision, current_digest, proposed_axis,
               proposed_digest, evidence_digest, independent_reviewer,
               evidence_source_ref, evidence_source_revision)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('EVENT_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        record = {'worker_id': worker_id, 'current_revision': current_revision,
                  'current_digest': current_digest, 'proposed_axis': proposed_axis,
                  'proposed_digest': proposed_digest, 'evidence_digest': evidence_digest,
                  'independent_reviewer': independent_reviewer,
                  'status': 'OWNER_APPROVAL_REQUIRED', 'permissions_effect': 'NONE'}
        return self._commit('personas', event_id, 0, record, event_id, fp,
                            {'persona_review': record})

    def apply_signed_notice(self, principal: str, signed_object: Mapping[str, Any]) -> Result:
        """Conditional bridge request; exact canonical verifier is PRIVATE and injected.

        NEVER creates a signed object. No port -> UNSUPPORTED. Any ambiguous
        apply returns PENDING_UNKNOWN, not success. One notice ID is consumed once
        and can be reconciled by the independently privileged gateway.
        """
        if not self._permitted(principal, 'notice_apply'):
            return _deny('NOTICE_APPLY_UNAUTHORIZED')
        if self._signed_notice is None or self._assignment is None or self._enforcement is None:
            return _deny('CANONICAL_NOTICE_OR_BRIDGE_UNAVAILABLE', Status.UNSUPPORTED)
        if not isinstance(signed_object, Mapping) or set(signed_object) != {'payload', 'signature_b64'}:
            return _deny('WRONG_SIGNED_TRANSPORT')
        payload = signed_object['payload']
        if not isinstance(payload, Mapping) or not isinstance(signed_object['signature_b64'], str):
            return _deny('INVALID_SIGNED_OBJECT')
        required = {'notice_id', 'action', 'worker_id', 'assignment_epoch', 'expires_at',
                    'owner_decision_ref', 'policy_revision', 'issuer_id'}
        if not required.issubset(payload):
            return _deny('NOTICE_FIELDS_MISSING')
        notice_id = payload['notice_id']
        action = payload['action']
        subject = payload['worker_id']
        epoch = payload['assignment_epoch']
        policy = payload['policy_revision']
        exp = payload['expires_at']
        if (not isinstance(notice_id, str) or not notice_id or not isinstance(subject, str) or
            not subject or not isinstance(payload['issuer_id'], str) or not payload['issuer_id'] or
            not isinstance(payload['owner_decision_ref'], str) or not payload['owner_decision_ref'] or
            action not in ALLOWED_ACTIONS or type(epoch) is not int or epoch < 0 or
            type(policy) is not int or policy < 1 or not isinstance(exp, (int, float)) or
            isinstance(exp, bool) or exp <= self._clock()):
            return _deny('INVALID_ACTION_SCOPE_OR_EXPIRY')
        # Verify the original exact signed bytes before trusting any payload field.
        if not self._port(self._signed_notice, 'verify', signed_object):
            return _deny('SIGNATURE_OR_TRUSTED_ISSUER_FAILED')
        if self._port(self._assignment, 'current_epoch', subject) != epoch:
            return _deny('STALE_ASSIGNMENT_EPOCH')
        current_rev, current_policy = self._policy()
        if current_policy is None or current_rev != policy:
            return _deny('STALE_HR_POLICY_REVISION')
        if action == 'REQUEST_LEASE_FENCE' or 'job_id' in payload or 'lease_epoch' in payload:
            job_id, lease_epoch = payload.get('job_id'), payload.get('lease_epoch')
            if not isinstance(job_id, str) or not job_id or type(lease_epoch) is not int or lease_epoch < 0:
                return _deny('LEASE_SCOPE_MISSING')
            if self._port(self._assignment, 'current_lease_epoch', subject, job_id) != lease_epoch:
                return _deny('STALE_OR_UNKNOWN_LEASE_EPOCH')
        if not self._owner_ok(payload['owner_decision_ref'], action, subject, policy):
            return _deny('OWNER_APPROVAL_NOT_VERIFIED', Status.OWNER_PENDING)
        try:
            fp = digest(dict(signed_object))
        except (TypeError, ValueError):
            return _deny('INVALID_SIGNED_JSON_PAYLOAD')
        eid = 'enforcement:' + notice_id
        try:
            prev = self._journal.receipt(eid, fp)
        except Conflict:
            return _deny('NOTICE_ID_COLLISION', Status.CONFLICT)
        if prev is not None:
            return _deny('NOTICE_REPLAY_REJECTED')
        # Persist intent BEFORE any side effect; failure is never silently replayed.
        prepared = {'notice_id': notice_id, 'action': action, 'worker_id': subject,
                    'assignment_epoch': epoch, 'policy_revision': policy,
                    'signed_object_digest': fp, 'outcome': 'PENDING_UNKNOWN'}
        result = self._commit('notice_intents', notice_id, 0, prepared, eid, fp,
                              {'notice_id': notice_id, 'outcome': 'PENDING_UNKNOWN'})
        if result.status != Status.OK:
            return result
        try:
            effect = self._enforcement.apply(signed_object)
            if effect == 'APPLIED' and self._enforcement.readback(notice_id, action, subject, epoch):
                return self._reconcile_verified_readback(notice_id, 'readback:' + notice_id)
        except Exception:
            pass
        return Result(Status.PENDING_UNKNOWN, 'BRIDGE_REQUIRES_AUTHORIZED_READBACK',
                      {'notice_id': notice_id, 'effect': 'UNKNOWN'})

    def reconcile_notice(self, principal: str, notice_id: str, event_id: str) -> Result:
        if not self._permitted(principal, 'notice_reconcile'):
            return _deny('NOTICE_RECONCILE_UNAUTHORIZED')
        return self._reconcile_verified_readback(notice_id, event_id)

    def _reconcile_verified_readback(self, notice_id: str, event_id: str) -> Result:
        if self._enforcement is None:
            return _deny('ENFORCEMENT_READBACK_UNAVAILABLE', Status.UNSUPPORTED)
        inp = (notice_id, 'readback_only')
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('RECONCILE_ID_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        rev, rec = self._journal.get('notice_intents', notice_id)
        if not rec:
            return _deny('UNKNOWN_NOTICE_INTENT')
        if rec['outcome'] == 'ENACTED_READBACK':
            return Result(Status.OK, 'PREVIOUSLY_CONFIRMED', {'notice_id': notice_id})
        try:
            confirmed = self._enforcement.readback(notice_id, rec['action'],
                                                   rec['worker_id'], rec['assignment_epoch'])
        except Exception:
            confirmed = False
        if not confirmed:
            return _deny('UNCONFIRMED_EXTERNAL_EFFECT', Status.PENDING_UNKNOWN)
        rec['outcome'] = 'ENACTED_READBACK'
        return self._commit('notice_intents', notice_id, rev, rec, event_id, fp,
                            {'notice_id': notice_id, 'effect': 'ENACTED_READBACK'})

    def prepare_archive(self, principal: str, case_id: str, event_id: str,
                        encrypted_archive_bytes: bytes, retention_class: str,
                        source_revision: str) -> Result:
        """Hash sealed archive bytes, ask external manifest attester; no deletion.

        'encrypted_archive_bytes' MUST originate with external sealed custodian.
        They are not saved by this component or sent to Studio.
        """
        rev, rec = self._read_case(principal, case_id, 'case_archive')
        if rev == -1:
            return _deny('ARCHIVE_UNAUTHORIZED')
        if (not isinstance(encrypted_archive_bytes, bytes) or not encrypted_archive_bytes or not source_revision or
            retention_class not in {'ACTIVE', 'LEGAL_HOLD', 'STANDARD', 'EXTENDED'}):
            return _deny('INVALID_ARCHIVE_METADATA')
        inp = (case_id, hashlib.sha256(encrypted_archive_bytes).hexdigest(), retention_class, source_revision)
        try:
            prev, fp = self._cached(event_id, inp)
        except Conflict:
            return _deny('ARCHIVE_EVENT_COLLISION', Status.CONFLICT)
        if prev:
            return prev
        if rec is None or not self._custody or not self._port(self._custody, 'scoped_access', case_id, principal):
            return _deny('ARCHIVE_CUSTODY_REQUIRED', Status.BLOCKED_PRIVATE_ACCESS)
        manifest = {'version': 'NOMAD/HR/ARCHIVE-MANIFEST/1',
                    'case_id': case_id,
                    'sealed_bytes_sha256': hashlib.sha256(encrypted_archive_bytes).hexdigest(),
                    'sealed_bytes_size': len(encrypted_archive_bytes),
                    'retention_class': retention_class, 'source_revision': source_revision,
                    'deletion_approved': False}
        if self._archive is None or not self._port(self._archive, 'attest_manifest', manifest):
            return _deny('INDEPENDENT_ARCHIVE_ATTESTATION_UNAVAILABLE', Status.NOT_CONNECTED)
        record = {'manifest': manifest, 'attestation': 'INJECTED_ATTESTER_ONLY',
                  'restore_witness': 'NOT_VERIFIED', 'deletion': 'FORBIDDEN'}
        return self._commit('archives', case_id + ':' + event_id, 0, record, event_id,
                            fp, {'manifest': manifest, 'attestation': 'INJECTED_ATTESTER_ONLY'})

    @staticmethod
    def verify_sealed_archive(manifest: Mapping[str, Any], encrypted_bytes: bytes) -> Result:
        if not isinstance(encrypted_bytes, bytes) or not isinstance(manifest, Mapping):
            return _deny('ARCHIVE_INPUT_INVALID')
        if (len(encrypted_bytes) != manifest.get('sealed_bytes_size') or
            hashlib.sha256(encrypted_bytes).hexdigest() != manifest.get('sealed_bytes_sha256')):
            return _deny('ARCHIVE_HASH_MISMATCH')
        return Result(Status.OK, 'ENCRYPTED_BYTES_INTEGRITY_ONLY',
                      {'restore_and_crypto_verification': 'NOT_VERIFIED'})

    def capacity_summary(self, synthetic_profiles: int, synthetic_events: int) -> Result:
        if synthetic_profiles < 0 or synthetic_events < 0:
            return _deny('INVALID_COUNTS')
        return Result(Status.OK, 'SYNTHETIC_RECORD_COUNTS_ONLY', {
            'synthetic_profiles': synthetic_profiles,
            'synthetic_events': synthetic_events,
            'real_chat_capacity': 'NOT_MEASURED',
            'live_provider_status': 'NOT_RUN'})


def build_component(deps: Mapping[str, object]) -> HRComponent:
    """No gateway registration, threads, network effects or provider calls."""
    return HRComponent(deps)
