"""R9 IdentityPort: root-anchored owner/worker/permission proofs.

`store` is a CAB-H table-only transaction capability injected by P2. `leases`
reads authoritative CAB-C lease state; no worker text grants permission.
"""
from __future__ import annotations
from typing import Any, Callable, Mapping
from .evidence import ProviderEvidenceVerifier, EvidenceError
from .port_types import Decision, Result, digest, ident, refused

class IdentityService:
    def __init__(self, *, verifier: ProviderEvidenceVerifier, store: Any,
                 clock_us: Callable[[], int], leases: Any, permission_reader: Any):
        self.verifier, self.store, self.clock_us = verifier, store, clock_us
        self.leases, self.permission_reader = leases, permission_reader

    def _identity(self, event: Any, *, kind: str) -> Result:
        claim = event.claims
        principal, credential, epoch = (claim.get('principal_id'),claim.get('credential_id'),claim.get('epoch'))
        now = self.clock_us()
        if (not ident(principal) or not ident(credential) or type(epoch) is not int or epoch < 0 or
            type(claim.get('valid_until_us')) is not int or claim['valid_until_us'] <= now or
            type(claim.get('issued_at_us')) is not int or claim['issued_at_us'] > now or
            claim.get('principal_kind') != kind):
            return refused('UNAUTHENTICATED')
        try:
            row = self.store.read_one('v1_verified_identities',
                'principal_id=? AND credential_id=?', (principal, credential))
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')
        if (not row or row['issuer_id'] != event.issuer_id or row['epoch'] != epoch or
            row['valid_until_us'] <= now or row['evidence_ref'] != claim.get('enrollment_ref')):
            return refused('UNAUTHENTICATED')
        return Result('OK','OK', record={'principal_id':principal,'credential_id':credential,
            'issuer_id':event.issuer_id,'epoch':epoch,'principal_kind':kind,
            'evidence_ref':event.ref,'simulated':False,
            'action_digest':claim.get('action_digest') if kind == 'OWNER' else None},receipt_ref=event.ref,evidence_refs=(event.ref,))

    def authenticate_owner(self, provider_proof_ref: str, action_digest: str) -> Result:
        if not digest(action_digest):
            return refused('INVALID_INPUT')
        try:
            proof = self.verifier.verify(provider_proof_ref,'OWNER_COMMAND',
                                         bindings={'action_digest':action_digest})
        except EvidenceError:
            return refused('UNAUTHENTICATED')
        return self._identity(proof,kind='OWNER')

    def authenticate_worker(self, provider_proof_ref: str, job_id: str, lease_epoch: int) -> Result:
        if not ident(job_id) or type(lease_epoch) is not int or lease_epoch < 0:
            return refused('INVALID_INPUT')
        try:
            proof = self.verifier.verify(provider_proof_ref,'WORKER_SESSION',
                bindings={'job_id':job_id,'lease_epoch':lease_epoch})
        except EvidenceError:
            return refused('UNAUTHENTICATED')
        result = self._identity(proof,kind='WORKER')
        if result.status != 'OK':
            return result
        try:
            lease = self.leases.read_active(job_id)
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')
        if (not isinstance(lease,Mapping) or lease.get('epoch') != lease_epoch or
            lease.get('principal_id') != result.record['principal_id'] or
            lease.get('expires_at_us',0) <= self.clock_us() or
            lease.get('session_id') != proof.claims.get('session_id')):
            return refused('LEASE_FENCED','CONFLICT')
        return result

    def verify_external_permission(self, principal_id: str, provider_id: str,
                                   action: str, target_ref: str) -> Decision:
        if not all(ident(x) for x in (principal_id,provider_id,action,target_ref)):
            return Decision(False,'DENY','INVALID_INPUT',str(target_ref))
        try:
            info = self.permission_reader.readback(principal_id,provider_id,action,target_ref)
            if not isinstance(info, Mapping) or not ident(info.get('proof_ref')):
                raise EvidenceError('permission lacks native readback')
            proof = self.verifier.verify(info['proof_ref'],'PROVIDER_PERMISSION',bindings={
                'principal_id':principal_id,'provider_id':provider_id,'action':action,
                'target_ref':target_ref})
            # A once-authorized external permission cannot outlive a revoked
            # principal credential or its rooted authority epoch.
            credential=proof.claims.get('credential_id')
            epoch=proof.claims.get('epoch')
            expiry=proof.claims.get('valid_until_us')
            if not ident(credential) or type(epoch) is not int or type(expiry) is not int or expiry <= self.clock_us():
                return Decision(False,'DENY','UNAUTHENTICATED',target_ref)
            stored=self.store.read_one('v1_verified_identities',
                'principal_id=? AND credential_id=?',(principal_id,credential))
            if (not stored or stored['epoch'] != epoch or stored['valid_until_us'] <= self.clock_us()):
                return Decision(False,'DENY','UNAUTHENTICATED',target_ref)
            allowed = proof.claims.get('allowed') is True
        except Exception:
            return Decision(False,'UNKNOWN','PROVIDER_UNKNOWN',target_ref)
        return Decision(allowed,'ALLOW' if allowed else 'DENY',
                        'OK' if allowed else 'PERMISSION_DENIED',target_ref,(proof.ref,))

    def verify_owner_decision(self, proof_ref: str, action: str,
                              subject_digest: str, project_id: str) -> Decision:
        if not ident(action) or not digest(subject_digest) or not ident(project_id):
            return Decision(False,'DENY','INVALID_INPUT',str(project_id))
        try:
            proof = self.verifier.verify(proof_ref,'OWNER_DECISION',bindings={
                'action':action,'subject_digest':subject_digest,'project_id':project_id})
        except EvidenceError:
            return Decision(False,'DENY','UNAUTHENTICATED',project_id)
        auth = self._identity(proof,kind='OWNER')
        if auth.status != 'OK':
            return Decision(False,'DENY','UNAUTHENTICATED',project_id)
        if proof.claims.get('allowed') is not True:
            return Decision(False,'DENY','PERMISSION_DENIED',project_id,(proof.ref,))
        return Decision(True,'ALLOW','OK',project_id,(proof.ref,))
