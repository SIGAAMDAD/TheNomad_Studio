"""Pure mediation workflow; all privileged identity, evidence and custody delegated."""
from __future__ import annotations
import hashlib
import json
import re
import secrets
from dataclasses import asdict
from typing import Optional, Sequence
from .models import (
    Appeal, AuditEntry, EvidenceAssessment, EvidenceRef, IssueReport, Mediator, Referral, Result,
    Session, TERMS, advanced,
)
from .ports import (CaseAccess, EvidenceAuthority, EmergencyGate, MediatorRegistry,
                    NoticeAuthority, RelationsStore)

_HEX64 = re.compile(r'^[0-9a-f]{64}$')
_HANDLE = re.compile(r'^[A-Za-z0-9_:/.-]{1,180}$')
_CASE = re.compile(r'^CASE-[0-9a-f]{32}$')
_WORKER = re.compile(r'^[A-Za-z0-9_.:-]{1,96}$')
_KINDS = frozenset({'WORK_DISAGREEMENT','COMMUNICATION_FRICTION','TARGETED_CONDUCT','IMPERSONATION','SECURITY_RISK'})
_CONFIRMABLE = frozenset({'OBSERVED','CORROBORATED'})


def _evidence_valid(e: EvidenceRef) -> bool:
    return bool(_HANDLE.fullmatch(e.handle) and _HEX64.fullmatch(e.sha256))


def _ref_valid(ref: str) -> bool:
    return bool(_HANDLE.fullmatch(ref))


def _request_digest(issue: IssueReport) -> str:
    payload = json.dumps(asdict(issue), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class RelationsComponent:
    """Private-port equivalent to a build_component(deps) facade.

    Caller-supplied actor names never grant case access: the external CaseAccess
    implementation must authenticate and authorize every operation independently.
    Missing ports or thrown faults always deny / UNSUPPORTED.
    """
    def __init__(self, *, store: Optional[RelationsStore] = None,
                 case_access: Optional[CaseAccess] = None,
                 evidence_authority: Optional[EvidenceAuthority] = None,
                 mediator_registry: Optional[MediatorRegistry] = None,
                 emergency_gate: Optional[EmergencyGate] = None,
                 notice_authority: Optional[NoticeAuthority] = None):
        self.store = store
        self.case_access = case_access
        self.evidence_authority = evidence_authority
        self.mediator_registry = mediator_registry
        self.emergency_gate = emergency_gate
        self.notice_authority = notice_authority

    def _authorized(self, actor: str, case_id: str, operation: str) -> str:
        if self.case_access is None:
            return 'UNSUPPORTED'
        if not isinstance(actor,str) or not _WORKER.fullmatch(actor):
            return 'DENIED'
        try:
            return 'OK' if self.case_access.permit(actor, case_id, operation) is True else 'DENIED'
        except Exception:
            return 'UNSUPPORTED'

    def _load(self, session_id: str, actor: str, operation: str) -> tuple[Optional[Session], Optional[Result]]:
        if self.store is None:
            return None, Result('UNSUPPORTED', 'PRIVATE_STORE_UNAVAILABLE')
        try:
            session = self.store.read(session_id)
        except Exception:
            return None, Result('UNSUPPORTED', 'PRIVATE_STORE_FAULT')
        if session is None:
            # Do not disclose whether a guessed confidential session ID exists.
            return None, Result('DENIED', 'NOT_FOUND_OR_CASE_ACCESS_NOT_GRANTED')
        auth = self._authorized(actor, session.issue.case_id, operation)
        if auth != 'OK':
            return None, Result(auth, 'NOT_FOUND_OR_CASE_ACCESS_NOT_GRANTED' if auth == 'DENIED' else 'CASE_ACCESS_UNAVAILABLE')
        return session, None

    def _save(self, prior: Session, changed: Session) -> Result:
        try:
            ok = self.store.update(prior.revision, changed)
        except Exception:
            return Result('UNSUPPORTED', 'PRIVATE_STORE_FAULT')
        return Result('OK' if ok else 'CONFLICT', 'UPDATED' if ok else 'STALE_REVISION',
                      changed.session_id if ok else None, changed.revision if ok else None,
                      changed.referral.route if ok else None)

    def _classify(self, issue: IssueReport) -> Referral:
        # Untrusted issue labels/evidence handles do not themselves verify conduct.
        assessment = EvidenceAssessment('CLAIMED')
        if self.evidence_authority is not None:
            try:
                observed = self.evidence_authority.assess(issue)
                if isinstance(observed, EvidenceAssessment) and observed.classification in (
                    'CLAIMED','OBSERVED','CORROBORATED','INFERRED','DISPUTED','INVALID_UNREADABLE'):
                    assessment = observed
            except Exception:
                pass  # Fail closed: do not promote a claim to verified fact.
        verified = assessment.classification in _CONFIRMABLE
        if issue.kind == 'WORK_DISAGREEMENT':
            return Referral('PRODUCTION_HEAD','TECHNICAL_OR_CREATIVE_DECISION', assessment.classification)
        if issue.kind == 'COMMUNICATION_FRICTION':
            if verified and assessment.recurring_events >= 2 and assessment.verified_impact:
                return Referral('MEDIATION','REPEATED_VERIFIED_WORKFLOW_FRICTION', assessment.classification)
            return Referral('PRODUCTION_HEAD','FRICTION_NOT_VERIFIED_RECURRING',assessment.classification)
        if issue.kind == 'TARGETED_CONDUCT':
            return Referral('HR_TRIAGE','CONDUCT_ALLEGATION_REQUIRES_REVIEW',assessment.classification)
        if issue.kind == 'IMPERSONATION':
            if verified and assessment.identity_abuse_verified:
                return Referral('INCIDENT_REVIEW','VERIFIED_IDENTITY_MISUSE',assessment.classification)
            return Referral('HR_TRIAGE','IDENTITY_CLAIM_UNVERIFIED',assessment.classification)
        if issue.kind == 'SECURITY_RISK':
            if verified and assessment.security_active_verified and self.emergency_gate is not None:
                try:
                    decision = self.emergency_gate.validate(issue, assessment)
                    if decision.preauthorized is True and decision.authorization_ref and _ref_valid(decision.authorization_ref):
                        return Referral('EMERGENCY_GATE_REVIEW','VALIDATED_RULE_REQUIRES_SEPARATE_GATE_ACTION',
                                        assessment.classification, decision.authorization_ref)
                except Exception:
                    pass
            return Referral('SECURITY_REVIEW_PENDING','NO_VALIDATED_EMERGENCY_AUTHORITY',assessment.classification)
        raise ValueError('invalid issue kind')

    def request(self, actor: str, request_id: str, issue: IssueReport) -> Result:
        if self.store is None:
            return Result('UNSUPPORTED','PRIVATE_STORE_UNAVAILABLE')
        if (not isinstance(issue,IssueReport) or not isinstance(request_id,str)
            or not _ref_valid(request_id) or not _CASE.fullmatch(issue.case_id)
            or issue.kind not in _KINDS or not all(isinstance(x,str) and _WORKER.fullmatch(x)
            for x in (issue.reporter_id,issue.subject_id,issue.responsible_head_id))
            or issue.reporter_id == issue.subject_id
            or not isinstance(issue.evidence,tuple)
            or not all(isinstance(e,EvidenceRef) and _evidence_valid(e) for e in issue.evidence)):
            return Result('INVALID','INVALID_OPAQUE_CASE_OR_EVIDENCE')
        if actor != issue.reporter_id:
            return Result('DENIED','REQUESTER_MUST_BE_REPORTER')
        auth = self._authorized(actor, issue.case_id, 'relations.request')
        if auth != 'OK':
            return Result(auth,'CASE_ACCESS_NOT_GRANTED')
        referral = self._classify(issue)
        sid = 'REL-'+secrets.token_hex(16)
        record = Session(session_id=sid, request_id=request_id, request_fingerprint=_request_digest(issue),
                         issue=issue, referral=referral,
                         status='OPEN' if referral.route == 'MEDIATION' else 'REFERRED',
                         audit=(AuditEntry('REQUEST_CREATED',actor,1),))
        try:
            state, actual = self.store.create(record)
        except Exception:
            return Result('UNSUPPORTED','PRIVATE_STORE_FAULT')
        if state == 'CONFLICT':
            return Result('CONFLICT','REQUEST_ID_REUSED_WITH_DIFFERENT_CONTENT')
        if state not in ('OK','REPLAY') or actual is None:
            return Result('UNSUPPORTED','UNKNOWN_STORAGE_RESULT')
        return Result(state,'CREATED' if state == 'OK' else 'IDEMPOTENT_REPLAY',
                      actual.session_id,actual.revision,actual.referral.route)

    def private_read(self, actor: str, session_id: str) -> Result:
        session, err = self._load(session_id,actor,'relations.private_read')
        if err: return err
        return Result('OK','PRIVATE_CASE_AUTHORIZED',session.session_id,session.revision,
                      session.referral.route,session)

    def studio_query(self, *args: object, **kwargs: object) -> Result:
        # No ambient Studio reader interface, even if the caller guesses an ID.
        return Result('DENIED','NO_CONFIDENTIAL_STUDIO_QUERY')

    def assign_mediator(self, actor: str, session_id: str) -> Result:
        old, err = self._load(session_id,actor,'relations.assign_mediator')
        if err: return err
        if old.referral.route != 'MEDIATION' or old.status not in ('OPEN','BLOCKED_INDEPENDENCE'):
            return Result('INVALID','NOT_OPEN_MEDIATION')
        if self.mediator_registry is None:
            return Result('UNSUPPORTED','MEDIATOR_REGISTRY_UNAVAILABLE')
        try:
            candidates = tuple(self.mediator_registry.candidates(old.issue.case_id))
        except Exception:
            return Result('UNSUPPORTED','MEDIATOR_REGISTRY_FAULT')
        parties = {old.issue.reporter_id,old.issue.subject_id,old.issue.responsible_head_id}
        eligible = sorted((c for c in candidates if isinstance(c, Mediator) and c.active is True
                           and c.qualified is True and old.issue.case_id in c.authorized_cases
                           and c.worker_id not in parties and old.issue.case_id not in c.conflict_cases
                           and not (parties & set(c.conflict_workers)) and not c.prior_case_roles),
                          key=lambda c: c.worker_id)
        if not eligible:
            if old.status == 'BLOCKED_INDEPENDENCE':
                return Result('BLOCKED_INDEPENDENCE','NO_CONFLICT_FREE_MEDIATOR',old.session_id,old.revision)
            saved = self._save(old,advanced(old,'INDEPENDENCE_BLOCKED',actor,status='BLOCKED_INDEPENDENCE'))
            return Result('BLOCKED_INDEPENDENCE','NO_CONFLICT_FREE_MEDIATOR',old.session_id,
                          saved.revision if saved.status=='OK' else None) if saved.status=='OK' else saved
        return self._save(old,advanced(old,'MEDIATOR_ASSIGNED',actor,mediator_id=eligible[0].worker_id,status='OPEN'))

    def participate(self, actor: str, session_id: str, choice: str) -> Result:
        old, err = self._load(session_id,actor,'relations.participate')
        if err: return err
        if old.referral.route != 'MEDIATION' or old.status not in ('OPEN','NOTICE_PENDING'):
            return Result('INVALID','NOT_OPEN_MEDIATION')
        if actor not in (old.issue.reporter_id,old.issue.subject_id):
            return Result('DENIED','NOT_A_PARTICIPANT')
        if choice not in ('ACCEPT','DECLINE'):
            return Result('INVALID','INVALID_PARTICIPATION_CHOICE')
        existing=dict(old.participation)
        if actor in existing:
            if existing[actor] == choice:
                return Result('REPLAY','PARTICIPATION_ALREADY_RECORDED',old.session_id,old.revision)
            return Result('CONFLICT','PARTICIPATION_CHANGE_REQUIRES_REVIEW')
        existing[actor]=choice
        return self._save(old,advanced(old,'PARTICIPATION_RECORDED',actor,participation=tuple(sorted(existing.items()))))

    def respond(self, actor: str, session_id: str, evidence: EvidenceRef, kind: str) -> Result:
        old, err = self._load(session_id,actor,'relations.respond')
        if err: return err
        if actor not in (old.issue.reporter_id,old.issue.subject_id):
            return Result('DENIED','NOT_A_PARTICIPANT')
        if old.referral.route != 'MEDIATION' or old.status not in ('OPEN','NOTICE_PENDING'):
            return Result('INVALID','NOT_OPEN_MEDIATION')
        if kind not in ('COUNTERSTATEMENT','FACT_RECORD') or not isinstance(evidence,EvidenceRef) or not _evidence_valid(evidence):
            return Result('INVALID','OPAQUE_REFERENCE_REQUIRED')
        if any(x.worker_id==actor and x.evidence==evidence and x.kind==kind for x in old.responses):
            return Result('REPLAY','RESPONSE_ALREADY_RECORDED',old.session_id,old.revision)
        from .models import Response
        return self._save(old,advanced(old,'RESPONSE_LINKED',actor,responses=old.responses+(Response(actor,evidence,kind),)))

    def set_working_contract(self, actor: str, session_id: str,
                             terms: Sequence[str], followup_checkpoint_ref: str) -> Result:
        old, err = self._load(session_id,actor,'relations.set_working_contract')
        if err: return err
        if actor != old.mediator_id or old.status != 'OPEN':
            return Result('DENIED','ACTIVE_MEDIATOR_REQUIRED')
        if not terms or len(terms)>8 or len(set(terms))!=len(terms) or any(t not in TERMS for t in terms) or not _ref_valid(followup_checkpoint_ref):
            return Result('INVALID','INVALID_STRUCTURED_WORKING_CONTRACT')
        if dict(old.participation).get(old.issue.reporter_id)!='ACCEPT' or dict(old.participation).get(old.issue.subject_id)!='ACCEPT':
            return Result('INVALID','BOTH_PARTICIPANTS_MUST_ACCEPT')
        return self._save(old,advanced(old,'WORKING_CONTRACT_DRAFTED',actor,
                                       working_terms=tuple(terms),followup_checkpoint_ref=followup_checkpoint_ref,
                                       status='NOTICE_PENDING'))

    def verify_notice(self, actor: str, session_id: str, notice_receipt_ref: str) -> Result:
        old, err = self._load(session_id,actor,'relations.verify_notice')
        if err: return err
        if actor != old.mediator_id or old.status!='NOTICE_PENDING':
            return Result('DENIED','ACTIVE_MEDIATOR_AND_NOTICE_REQUIRED')
        if not _ref_valid(notice_receipt_ref):
            return Result('INVALID','INVALID_NOTICE_RECEIPT')
        if self.notice_authority is None:
            return Result('UNSUPPORTED','NOTICE_VERIFIER_UNAVAILABLE')
        try:
            approved = self.notice_authority.verify_delivery(old.issue.case_id,notice_receipt_ref,
                                                               (old.issue.reporter_id,old.issue.subject_id))
        except Exception:
            return Result('UNSUPPORTED','NOTICE_VERIFIER_FAULT')
        if approved is not True:
            return Result('DENIED','NOTICE_DELIVERY_NOT_VERIFIED')
        return self._save(old,advanced(old,'NOTICE_RECEIPT_VERIFIED',actor,
                                       notice_receipt_ref=notice_receipt_ref,status='READY'))

    def close(self, actor: str, session_id: str, resolved: bool) -> Result:
        old, err = self._load(session_id,actor,'relations.close')
        if err: return err
        if old.referral.route != 'MEDIATION':
            return Result('DENIED','REFERRAL_MANAGED_BY_DESTINATION_AUTHORITY')
        if actor != old.mediator_id or old.status not in ('OPEN','READY','NOTICE_PENDING'):
            return Result('DENIED','ACTIVE_MEDIATOR_REQUIRED')
        if resolved:
            if (old.status!='READY' or not old.notice_receipt_ref or not old.working_terms
                or not old.followup_checkpoint_ref or set(dict(old.participation).values())!={'ACCEPT'}
                or len(old.participation)!=2):
                return Result('INVALID','RESOLUTION_PREREQUISITES_MISSING')
            return self._save(old,advanced(old,'MEDIATION_RESOLVED',actor,status='RESOLVED'))
        return self._save(old,advanced(old,'MEDIATION_UNRESOLVED',actor,status='UNRESOLVED',
                                       escalation_route='HR_TRIAGE'))

    def appeal(self, actor: str, session_id: str, appeal_id: str, new_evidence: EvidenceRef) -> Result:
        old, err = self._load(session_id,actor,'relations.appeal')
        if err: return err
        if actor not in (old.issue.reporter_id,old.issue.subject_id):
            return Result('DENIED','ONLY_AFFECTED_PARTY_MAY_APPEAL')
        if old.status not in ('RESOLVED','UNRESOLVED','REFERRED','APPEALED'):
            return Result('INVALID','APPEAL_REQUIRES_DISPOSITION')
        if not _ref_valid(appeal_id) or not isinstance(new_evidence,EvidenceRef) or not _evidence_valid(new_evidence):
            return Result('INVALID','INVALID_APPEAL_REFERENCE')
        for a in old.appeals:
            if a.appeal_id==appeal_id:
                return (Result('REPLAY','APPEAL_ALREADY_RECORDED',old.session_id,old.revision)
                        if a.submitted_by==actor and a.new_evidence==new_evidence
                        else Result('CONFLICT','APPEAL_ID_REUSED'))
        if self.mediator_registry is None:
            return Result('UNSUPPORTED','INDEPENDENT_REVIEWER_REGISTRY_UNAVAILABLE')
        try:
            candidates = tuple(self.mediator_registry.candidates(old.issue.case_id))
        except Exception:
            return Result('UNSUPPORTED','INDEPENDENT_REVIEWER_REGISTRY_FAULT')
        exclude={old.issue.reporter_id,old.issue.subject_id,old.issue.responsible_head_id,old.mediator_id}
        exclude.update(a.reviewer_id for a in old.appeals)
        selected=sorted((c for c in candidates if isinstance(c,Mediator) and c.active is True
                         and c.qualified is True and old.issue.case_id in c.authorized_cases
                         and c.worker_id not in exclude and old.issue.case_id not in c.conflict_cases
                         and not (exclude & set(c.conflict_workers)) and not c.prior_case_roles),
                        key=lambda c: c.worker_id)
        if not selected:
            return Result('BLOCKED_INDEPENDENCE','NO_INDEPENDENT_APPEAL_REVIEWER',old.session_id,old.revision)
        new=Appeal(appeal_id,actor,new_evidence,selected[0].worker_id)
        return self._save(old,advanced(old,'APPEAL_PENDING_EXTERNAL_ROUTE',actor,
                                       appeals=old.appeals+(new,),status='APPEALED'))

