"""Strict closed notice codec; no case content in signed claims or receipts."""
from __future__ import annotations
import hashlib
import json
import re
from typing import Any, Mapping

ACTIONS = frozenset({"HOLD_JOB", "QUARANTINE_WORKER", "REVOKE_WORKER_CAPABILITY"})
REASONS = frozenset({"CREDENTIAL_COMPROMISE", "INCIDENT_RECOVERY", "ISSUER_ROTATION", "OVERSIGHT_WITHDRAWAL"})
NOTICE_FIELDS = frozenset({"claims", "key_id", "signature"})
CLAIM_FIELDS = frozenset({"notice_id", "issuer_id", "worker_id", "job_id", "assignment_epoch",
                          "action", "scope", "issued_at_us", "expires_at_us", "case_ref_sha256"})
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
HEX_RE = re.compile(r"^[a-f0-9]{64}$")
SIG_RE = re.compile(r"^[A-Za-z0-9_-]{43,2048}={0,2}$")

class InvalidNotice(ValueError):
    pass

def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                          allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise InvalidNotice("non-canonical payload") from error

def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

def ident(value: Any) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None

def integer(value: Any) -> bool:
    return type(value) is int and value >= 0 and value <= 2**63-1

def inspect_notice(value: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping) or set(value) != NOTICE_FIELDS:
        raise InvalidNotice("invalid signed notice shape")
    if not ident(value["key_id"]) or not isinstance(value["signature"], str) or not SIG_RE.fullmatch(value["signature"]):
        raise InvalidNotice("missing/invalid signature metadata")
    claims = value["claims"]
    if not isinstance(claims, Mapping) or set(claims) != CLAIM_FIELDS:
        raise InvalidNotice("invalid notice claims")
    for k in ("notice_id", "issuer_id", "worker_id", "job_id"):
        if not ident(claims[k]):
            raise InvalidNotice("invalid notice claim identifier")
    if claims["scope"] != "worker_job_epoch" or claims["action"] not in ACTIONS:
        raise InvalidNotice("invalid notice scope or action")
    for k in ("assignment_epoch", "issued_at_us", "expires_at_us"):
        if not integer(claims[k]):
            raise InvalidNotice("invalid notice epoch/timestamp")
    if claims["expires_at_us"] <= claims["issued_at_us"]:
        raise InvalidNotice("invalid notice expiry")
    if not isinstance(claims["case_ref_sha256"], str) or not HEX_RE.fullmatch(claims["case_ref_sha256"]):
        raise InvalidNotice("invalid sealed case reference digest")
    # Every field is checked before any backend call; never materialize complaint data.
    copy = {"claims": dict(claims), "key_id": value["key_id"], "signature": value["signature"]}
    return dict(claims), digest(copy)
