"""Contract-level errors; untrusted service exceptions never cross HTTP verbatim."""
from __future__ import annotations

from dataclasses import dataclass

ERROR_CODES = frozenset({
    'invalid', 'missing', 'forbidden', 'conflict', 'stale_revision',
    'stale_snapshot', 'requires_owner', 'capability_unavailable',
    'qa_failed', 'provider_unavailable', 'unknown_effect', 'internal',
})
STATUS_CODES = {
    'invalid': 400, 'missing': 404, 'forbidden': 403, 'conflict': 409,
    'stale_revision': 409, 'stale_snapshot': 409, 'requires_owner': 403,
    'capability_unavailable': 503, 'qa_failed': 409,
    'provider_unavailable': 503, 'unknown_effect': 409, 'internal': 500,
}


@dataclass
class ApiError(Exception):
    code: str
    message: str
    status: int | None = None
    related_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError('unsupported OpenAPI error code')
        # Exceptions must allow Python to attach traceback/cause/context when
        # unwinding generators and context-managed APSW transactions.
        super().__init__(self.message)

    def envelope(self, request_id: str | None) -> dict:
        return {
            'code': self.code,
            'message': self.message,
            'request_id': request_id,
            'related_refs': list(self.related_refs),
        }

    @property
    def http_status(self) -> int:
        return self.status if self.status is not None else STATUS_CODES[self.code]
