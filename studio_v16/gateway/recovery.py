"""Internal recovery-only gateway. Providers are used exclusively for readback."""
from __future__ import annotations

import re

from studio_v16.db.publication import PublicationDenied
from studio_v16.db.sqlite.recovery import TrustedRecoveryIdentity
from .contracts import validate
from .gateway import Gateway, AdmissionError
from .interfaces import Principal
from studio_v16.providers.git_provider import ProviderBlocked


class RecoveryGateway:
    def __init__(self, *, store, git=None, drive=None, audit=None):
        self._store = store
        self._git, self._drive = git, drive
        self._receipts = Gateway(audit=audit)

    def reconcile(self, publication_id, idempotency_key, principal: Principal | None):
        """Authenticated server route; accepts no worker readback or grant fields."""
        if (principal is None or principal.auth_method != 'MTLS'
                or not isinstance(publication_id, str) or not 1 <= len(publication_id) <= 180
                or not isinstance(idempotency_key, str) or not re.fullmatch('[a-f0-9]{64}', idempotency_key)):
            raise AdmissionError('IDENTITY_UNVERIFIED')
        identity = TrustedRecoveryIdentity(principal.principal_id, verified_gateway=True)
        try:
            intent, dispatch = self._store.recorded_dispatch(publication_id, idempotency_key, identity)
        except PublicationDenied as exc:
            raise AdmissionError(exc.code) from None
        provider = intent['target']['provider']
        source = self._git if provider == 'GITHUB' else self._drive
        if source is None:
            raise AdmissionError('GATEWAY_UNAVAILABLE')
        # Network reads occur after the database read connection has closed.
        # An exception cannot prove absence and cannot authorize another write.
        absence = None
        provider_conflict = False
        try:
            classify = getattr(source, 'recovery_readback', None)
            if classify is None:
                observed = source.readback(intent)
            else:
                facts = classify(intent)
                if facts.get('state') not in ('PRESENT', 'ABSENT', 'CONFLICT', 'UNKNOWN'):
                    raise AdmissionError('GATEWAY_UNAVAILABLE')
                observed = facts.get('observed') if facts['state'] == 'PRESENT' else None
                absence = facts.get('evidence') if facts['state'] == 'ABSENT' else None
                provider_conflict = facts['state'] == 'CONFLICT'
        except (TimeoutError, ConnectionError, LookupError, ProviderBlocked):
            observed = None
        if provider_conflict:
            status, code = 'CONFLICT', 'SOURCE_SHA_MISMATCH' if provider == 'GITHUB' else 'DOCUMENT_CONFLICT'
        elif observed is None:
            status, code = 'EXTERNAL_OBSERVED', None
        else:
            candidate = {'provider': provider, 'observed': observed}
            exact = self._store._exact_observation(intent, candidate)
            status = 'VERIFIED' if exact else 'CONFLICT'
            code = None if exact else ('SOURCE_SHA_MISMATCH' if provider == 'GITHUB' else 'DOCUMENT_CONFLICT')
        event = self._receipts._audit('RECOVERY_READBACK', status, code, publication_id, principal)
        receipt = self._receipts._receipt(publication_id, idempotency_key, provider, status, code, observed, event)
        validate(receipt, 'PUBLISH-V16/READBACK/1')
        trusted = TrustedRecoveryIdentity(principal.principal_id, verified_gateway=True, provider_readback_verified=True)
        try:
            result = self._store.reconcile_dispatched_effect(publication_id, idempotency_key, dispatch, receipt, trusted,
                                                            absence_evidence=absence)
        except PublicationDenied as exc:
            raise AdmissionError(exc.code) from None
        if result not in ('VERIFIED', 'CONFLICT', 'RECONCILE_REQUIRED'):
            raise AdmissionError('RETRYABLE_BLOCKED')
        return receipt
