"""WG-D Authority adapter backed by WG-C transactional publication storage.

An independently authenticated authority evidence reader MUST supply the State.
Without it the adapter cannot authorize effects or construct forged grants.
"""
from __future__ import annotations

from studio_v16.gateway.interfaces import State
from studio_v16.gateway.gateway import AdmissionError
from studio_v16.db.publication import PublicationDenied


class PostgresAuthority:
    def __init__(self, store, *, authoritative_evidence=None):
        self._store = store
        self._evidence = authoritative_evidence

    def load(self, job_id: str, grant_id: str) -> State:
        if self._evidence is None:
            raise LookupError('DURABLE_AUTHORIZATION_EVIDENCE_UNAVAILABLE')
        state = self._evidence.load(job_id, grant_id)
        if not isinstance(state, State):
            raise LookupError('AUTHORITY_EVIDENCE_INVALID')
        try:
            self._store.verify_gateway_state(job_id, grant_id, state)
        except PublicationDenied as exc:
            # The gateway must audit the actual live-DB denial (revocation,
            # stale lease, scope drift), rather than mislabeling it as an outage.
            raise AdmissionError(exc.code) from None
        return state

    def reserve(self, intent: dict, principal_id: str, state_revision: str, payload_sha256: str) -> str:
        try:
            state = self.load(intent['job_id'], intent['grant_id'])
            return self._store.reserve_gateway_intent(intent,principal_id,state,state_revision,payload_sha256)
        except AdmissionError as exc:
            return exc.code
        except (LookupError, KeyError):
            return 'GATEWAY_UNAVAILABLE'

    def recorded(self, publication_id: str, idempotency_key: str) -> dict | None:
        reader=getattr(self._store,'recorded_gateway_intent',None)
        return reader(publication_id,idempotency_key) if reader is not None else None

    def reconcile(self, publication_id: str, idempotency_key: str,
                  state_revision: str, receipt: dict) -> bool:
        try:
            intent = self.recorded(publication_id,idempotency_key)
            if intent is None:
                return False
            state = self.load(intent['job_id'],intent['grant_id'])
            return self._store.reconcile_gateway_intent(publication_id,idempotency_key,
                                                       state,state_revision,receipt)
        except (AdmissionError, LookupError, KeyError):
            return False

    def authorize_effect(self, intent: dict) -> bool:
        """Provider admission callback; only the isolated publisher calls it."""
        try:
            state=self.load(intent['job_id'],intent['grant_id'])
            return self._store.authorize_gateway_effect(intent,state)
        except (AdmissionError, LookupError, KeyError):
            return False

    def mark_conflict(self, publication_id: str, idempotency_key: str,
                      state_revision: str, receipt: dict) -> bool:
        try:
            intent=self.recorded(publication_id,idempotency_key)
            if intent is None:
                return False
            state=self.load(intent['job_id'],intent['grant_id'])
            return self._store.mark_gateway_conflict(publication_id,idempotency_key,
                                                     state,state_revision,receipt)
        except (AdmissionError, LookupError, KeyError):
            return False
