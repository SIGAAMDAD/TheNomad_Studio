"""Trusted HTTP identity boundary: bearer proofs are never taken from body/relay JSON."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Mapping, Protocol, Iterable

from .errors import ApiError


@dataclass(frozen=True)
class VerifiedPrincipal:
    principal_id: str
    issuer: str
    subject: str
    trusted_proof_ref: str
    owner: bool
    kind: str = 'human'

    def __post_init__(self) -> None:
        if not all([self.principal_id, self.issuer, self.subject, self.trusted_proof_ref]):
            raise ValueError('incomplete verified principal')
        if self.kind not in {'human', 'service'} or (self.owner and self.kind != 'human'):
            raise ValueError('only verified humans may be owners')


class IdentityVerifier(Protocol):
    def verify_bearer(self, credential: str) -> VerifiedPrincipal: ...


class ProjectGrantsReader(Protocol):
    """Reads *current* trusted grant rows; revocations take effect next request."""
    def granted_project_ids(self, principal_id: str) -> Iterable[str]: ...


class PrincipalRegistrationStore(Protocol):
    """WG-C scoped named writer for J-owned principals; idempotent issuer/subject.

    Must enforce immutable (principal_id, issuer, subject, kind) identity binding,
    unique (issuer,subject) and reject collisions; no untrusted caller fields.
    """
    def ensure_trusted_principal(self, *, principal_id: str,
                                 auth_issuer: str, auth_subject: str, kind: str) -> None: ...


class PrincipalProvisioner:
    """Provision authenticated principals on the trusted service startup path.

    This has deliberately no HTTP route. A boot/bootstrap orchestrator invokes
    it using trusted provisioned credentials before the first project creation.
    It must run through WG-C's scoped physical writer, not direct SQLite.
    """
    def __init__(self, verifier: IdentityVerifier, store: PrincipalRegistrationStore):
        self.verifier = verifier
        self.store = store

    def provision_bearer(self, credential: str) -> VerifiedPrincipal:
        principal = self.verifier.verify_bearer(credential)
        self.store.ensure_trusted_principal(
            principal_id=principal.principal_id, auth_issuer=principal.issuer,
            auth_subject=principal.subject, kind=principal.kind,
        )
        return principal


class ConfiguredOpaqueVerifier:
    """Optional verifier for local/self-hosted operator authentication.

    Credential -> VerifiedPrincipal entries must be provisioned by a trusted
    administrator, not read from a browser request, Github relay, or DB worker
    claim. Retains only SHA-256 token digests in memory; use high-entropy tokens
    provisioned via private configuration. A real OIDC verifier may implement
    IdentityVerifier instead, without changing routing or auth.
    """

    def __init__(self, sha256_entries: Mapping[str, VerifiedPrincipal]):
        if not sha256_entries:
            raise ValueError('empty identity configuration')
        if any(len(key) != 64 or any(c not in '0123456789abcdef' for c in key) for key in sha256_entries):
            raise ValueError('expected SHA-256 hex credential IDs')
        self._entries = dict(sha256_entries)

    def verify_bearer(self, credential: str) -> VerifiedPrincipal:
        if not credential or len(credential) > 8192:
            raise ApiError('forbidden', 'invalid authentication proof', status=401)
        digest = hashlib.sha256(credential.encode('utf-8')).hexdigest()
        for candidate, identity in self._entries.items():
            if hmac.compare_digest(digest, candidate):
                return identity
        raise ApiError('forbidden', 'invalid authentication proof', status=401)


def authenticated_actor(
    verifier: IdentityVerifier, grants: ProjectGrantsReader, credential: str,
    project_id: str | None, required_auth: str,
) -> tuple[dict, VerifiedPrincipal]:
    principal = verifier.verify_bearer(credential)
    # Project membership is read afresh from the *trusted* owner-grant store.
    allowed = frozenset(grants.granted_project_ids(principal.principal_id))
    if not all(isinstance(p, str) and p for p in allowed):
        raise ApiError('internal', 'invalid trusted grants')
    if required_auth == 'owner' and not principal.owner:
        raise ApiError('requires_owner', 'verified human owner required')
    if project_id is not None and project_id not in allowed:
        # Do not expose whether the requested project exists.
        raise ApiError('forbidden', 'project unavailable')
    return ({
        'principal_id': principal.principal_id,
        'issuer': principal.issuer,
        'allowed_project_ids': sorted(allowed),
        'trusted_proof_ref': principal.trusted_proof_ref,
    }, principal)
