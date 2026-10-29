"""R9 frozen port value shapes and controlled operation result vocabulary.

P2 may use these structural values directly or translate into its canonical
runtime_ports_v1.Result/Decision dataclasses (same fields and ordering).
"""
from dataclasses import dataclass
from typing import Any, Mapping

REASONS = frozenset({
    'OK','ACCEPTED_PENDING','INVALID_INPUT','NOT_FOUND','UNAUTHENTICATED',
    'PERMISSION_DENIED','WRONG_PROJECT','WRONG_FEATURE','STALE_BIBLE',
    'BIBLE_CONFLICT','SCOPE_EXCEEDED','OWNER_ACTION_REQUIRED','REVISION_CONFLICT',
    'IDEMPOTENCY_CONFLICT','LEASE_CONFLICT','LEASE_FENCED','SOURCE_CONFLICT',
    'PROVIDER_UNKNOWN','PROVIDER_UNSUPPORTED','PROVIDER_NOT_VERIFIED',
    'TEST_FAILED','TEST_NOT_RUN','EVIDENCE_INVALID','REVIEW_CONFLICT',
    'CAPACITY_LIMIT','BACKPRESSURE','LOCAL_RECOVERY_REQUIRED',
    'UNSUPPORTED_OPERATION','RELEASE_BLOCKED',
})
RESULT_STATUSES = frozenset({'OK','ACCEPTED','REJECTED','CONFLICT','NOT_FOUND',
    'BLOCKED_LOCAL','RETRYABLE','UNKNOWN_EXTERNAL','NOT_VERIFIED','UNSUPPORTED'})
DECISION_STATUSES = frozenset({'ALLOW','DENY','OWNER_ACTION_REQUIRED','HOLD_LOCAL','UNKNOWN'})

from nomad_studio.contracts.runtime_ports_v1 import Result, Decision


