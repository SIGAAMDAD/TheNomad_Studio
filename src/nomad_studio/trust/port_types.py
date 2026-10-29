"""CAB-H structural R9 Result/Decision types and strict receipt utilities.

P2 may use the canonical frozen definitions instead: fields and result vocabulary
are identical. These are not historical command-envelope adapters.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping

RESULT_STATUSES = frozenset({'OK','ACCEPTED','REJECTED','CONFLICT','NOT_FOUND',
    'BLOCKED_LOCAL','RETRYABLE','UNKNOWN_EXTERNAL','NOT_VERIFIED','UNSUPPORTED'})
DECISION_STATUSES = frozenset({'ALLOW','DENY','OWNER_ACTION_REQUIRED','HOLD_LOCAL','UNKNOWN'})
CODES = frozenset({'OK','ACCEPTED_PENDING','INVALID_INPUT','NOT_FOUND','UNAUTHENTICATED',
    'PERMISSION_DENIED','WRONG_PROJECT','WRONG_FEATURE','STALE_BIBLE','BIBLE_CONFLICT',
    'SCOPE_EXCEEDED','OWNER_ACTION_REQUIRED','REVISION_CONFLICT','IDEMPOTENCY_CONFLICT',
    'LEASE_CONFLICT','LEASE_FENCED','SOURCE_CONFLICT','PROVIDER_UNKNOWN',
    'PROVIDER_UNSUPPORTED','PROVIDER_NOT_VERIFIED','TEST_FAILED','TEST_NOT_RUN',
    'EVIDENCE_INVALID','REVIEW_CONFLICT','CAPACITY_LIMIT','BACKPRESSURE',
    'LOCAL_RECOVERY_REQUIRED','UNSUPPORTED_OPERATION','RELEASE_BLOCKED'})
IDENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
SHA = re.compile(r'^[a-f0-9]{64}$')

from nomad_studio.contracts.runtime_ports_v1 import Result, Decision


def ident(value: Any) -> bool:
    return isinstance(value, str) and IDENT.fullmatch(value) is not None

def digest(value: Any) -> bool:
    return isinstance(value, str) and SHA.fullmatch(value) is not None

def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')

def sha(value: Any) -> str:
    return sha256(canonical(value)).hexdigest()

def refused(code: str = 'EVIDENCE_INVALID', status: str = 'REJECTED') -> Result:
    return Result(status, code)
