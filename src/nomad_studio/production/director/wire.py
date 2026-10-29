"""Strict R9 result and canonical digest helpers (no legacy aliases)."""
from __future__ import annotations
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

# P2 must expose frozen runtime_ports_v1.Result at composition time. The
# structure is identical, with runtime import fallback only for isolated tests
# lacking a separately packaged frozen-contract module.
from nomad_studio.contracts.runtime_ports_v1 import Result, Record

_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
_SHA = re.compile(r'^[a-f0-9]{40}(?:[a-f0-9]{24})?$')
_DIGEST = re.compile(r'^[a-f0-9]{64}$')


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def valid_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def valid_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(_DIGEST.fullmatch(value))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def ok(record: Record, receipt_ref: str | None = None) -> Result:
    return Result('OK', 'OK', dict(record), receipt_ref)


def fail(status: str, code: str) -> Result:
    return Result(status, code)
