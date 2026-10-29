"""Verify externally read-back, root-anchored provider and owner attestations.

The injected reader must consult the native host/provider, not a model-authored
record. A test/simulated anchor NEVER grants production host capability. Root is
responsible for provisioning and protecting anchor keys and reader credentials.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import hmac
from typing import Any, Callable, Mapping
from .port_types import canonical, ident

@dataclass(frozen=True, slots=True)
class TrustAnchor:
    issuer_id: str
    provider_id: str
    purpose: frozenset[str]
    secret: bytes
    production: bool = False
    def __post_init__(self):
        if not ident(self.issuer_id) or not ident(self.provider_id) or len(self.secret) < 32:
            raise ValueError('invalid trust anchor')

@dataclass(frozen=True, slots=True)
class VerifiedEvidence:
    ref: str
    issuer_id: str
    provider_id: str
    purpose: str
    claims: Mapping[str, Any]
    production: bool

class EvidenceError(ValueError):
    pass

class ProviderEvidenceVerifier:
    def __init__(self, read_native: Callable[[str], Mapping[str, Any] | None],
                 anchors: Mapping[str, TrustAnchor]):
        if not callable(read_native):
            raise TypeError('independent native readback required')
        self._reader = read_native
        self._anchors = dict(anchors)

    def verify(self, ref: str, purpose: str, *, bindings: Mapping[str, Any] | None = None,
               require_production: bool = True) -> VerifiedEvidence:
        if not ident(ref):
            raise EvidenceError('invalid evidence reference')
        try:
            signed = self._reader(ref)
        except Exception as exc:
            raise EvidenceError('provider readback unavailable') from exc
        if not isinstance(signed, Mapping) or set(signed) != {'issuer_id','purpose','claims','signature'}:
            raise EvidenceError('invalid native envelope')
        issuer, observed_purpose, claims, signature = (signed[k] for k in
                                                         ('issuer_id','purpose','claims','signature'))
        anchor = self._anchors.get(issuer)
        if (not anchor or purpose != observed_purpose or purpose not in anchor.purpose or
            not isinstance(claims, Mapping) or not isinstance(signature, str) or
            len(signature) != 64 or any(c not in '0123456789abcdef' for c in signature)):
            raise EvidenceError('unknown issuer or claim')
        if require_production and not anchor.production:
            raise EvidenceError('test issuer is never production evidence')
        if claims.get('simulated') is not False or claims.get('provider_id') != anchor.provider_id:
            raise EvidenceError('simulation/issuer mismatch')
        try:
            payload = {'issuer_id': issuer, 'purpose': purpose, 'claims': dict(claims)}
            actual = hmac.new(anchor.secret, canonical(payload), sha256).hexdigest()
        except (TypeError, ValueError, UnicodeError) as exc:
            raise EvidenceError('noncanonical claims') from exc
        if not hmac.compare_digest(signature, actual):
            raise EvidenceError('forged provider signature')
        if bindings and any(claims.get(k) != v for k, v in bindings.items()):
            raise EvidenceError('receipt does not bind requested operation')
        if not ident(claims.get('native_ref')) or claims['native_ref'] != ref:
            raise EvidenceError('no bound native receipt identity')
        return VerifiedEvidence(ref, issuer, anchor.provider_id, purpose,
                                dict(claims), anchor.production)
