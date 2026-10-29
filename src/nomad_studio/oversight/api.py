"""R9 OversightNoticePort: signed, minimal, sealed notice ingress only.

This module never imports private Oversight stores and never writes case data.
Only a current job/worker/epoch action is admitted. P2 owns durable dedup,
audit, outbox and trusted enforcement via injected narrow notice sink.
"""
from __future__ import annotations
from typing import Any, Callable, Mapping
from nomad_studio.trust.port_types import Result, ident, sha, refused
from nomad_studio.trust.evidence import ProviderEvidenceVerifier, EvidenceError
from .codec import CLAIM_FIELDS, inspect_notice, InvalidNotice

class OversightNoticeService:
    def __init__(self, *, verifier: ProviderEvidenceVerifier, assignment_reader: Any,
                 sink: Any, clock_us: Callable[[], int], max_notice_lifetime_us: int = 3_600_000_000):
        if type(max_notice_lifetime_us) is not int or max_notice_lifetime_us < 1:
            raise ValueError('invalid notice lifetime')
        self.verifier, self.assignment_reader, self.sink = verifier,assignment_reader,sink
        self.clock_us, self.max_notice_lifetime_us = clock_us,max_notice_lifetime_us

    def ingest_signed_notice(self, notice: Mapping[str, Any], trusted_signature: Mapping[str, Any]) -> Result:
        if (not isinstance(notice, Mapping) or set(notice) != CLAIM_FIELDS or
            not isinstance(trusted_signature, Mapping) or
            set(trusted_signature) != {'key_id','signature','proof_ref'} or
            not ident(trusted_signature.get('proof_ref'))):
            return refused('INVALID_INPUT')
        sealed={'claims':dict(notice),'key_id':trusted_signature['key_id'],
                'signature':trusted_signature['signature']}
        try:
            claims, signed_digest = inspect_notice(sealed)
        except InvalidNotice:
            return refused('INVALID_INPUT')
        now=self.clock_us()
        if (not claims['issued_at_us'] <= now < claims['expires_at_us'] or
            claims['expires_at_us'] - claims['issued_at_us'] > self.max_notice_lifetime_us):
            return refused('EVIDENCE_INVALID')
        try:
            proof=self.verifier.verify(trusted_signature['proof_ref'],'OVERSIGHT_NOTICE',bindings={
                'notice_digest':signed_digest,'notice_id':claims['notice_id'],
                'worker_id':claims['worker_id'],'job_id':claims['job_id'],
                'assignment_epoch':claims['assignment_epoch']})
            if (proof.claims.get('key_id') != trusted_signature['key_id'] or
                proof.claims.get('notice_signature') != trusted_signature['signature'] or
                proof.claims.get('issuer_id') != claims['issuer_id']):
                return refused('EVIDENCE_INVALID')
        except EvidenceError:
            return refused('EVIDENCE_INVALID')
        try:
            assignment=self.assignment_reader.current_assignment(claims['worker_id'],claims['job_id'])
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')
        if (not isinstance(assignment,Mapping) or assignment.get('epoch') != claims['assignment_epoch'] or
            assignment.get('worker_id') != claims['worker_id'] or
            assignment.get('job_id') != claims['job_id'] or
            assignment.get('fenced') is True):
            return refused('LEASE_FENCED','CONFLICT')
        # Only the closed claims (no private case data) cross the boundary.
        # The P2-owned sink binds digest + notice_id to its durable journal.
        try:
            result=self.sink.accept(dict(claims),signed_digest,proof.ref)
        except Exception:
            return refused('PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
        if not isinstance(result,Result):
            return refused('PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
        if result.status not in {'OK','ACCEPTED'}:
            return result
        if result.receipt_ref is None:
            return refused('PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
        return Result(result.status,result.code,
                      record={'notice_id':claims['notice_id'],'action':claims['action'],
                              'worker_id':claims['worker_id'],'job_id':claims['job_id'],
                              'assignment_epoch':claims['assignment_epoch']},
                      receipt_ref=result.receipt_ref,
                      evidence_refs=(proof.ref,)+tuple(result.evidence_refs))
