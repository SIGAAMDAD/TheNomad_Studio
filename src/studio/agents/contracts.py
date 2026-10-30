"""Frozen R5 GitHub relay-v1 wire rules. No inference/model execution."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any

ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,95}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
REF = re.compile(r"^sha256:[a-f0-9]{64}$")
KINDS = frozenset(("HELLO", "ASSIGN", "NOTE", "QUESTION", "ANSWER", "EVIDENCE", "REVIEW", "HANDOFF", "STOP", "STATUS"))
RECEIPT_KINDS = frozenset(("SEEN", "CHECKPOINT", "SUBMITTED", "STOP_SEEN"))
MESSAGE_KEYS = frozenset(("protocol_version", "message_id", "project_id", "channel_id", "from_session_id", "to_session_id", "task_id", "lease_generation", "kind", "issued_at", "payload_digest", "payload_ref", "canon_snapshot_id", "contract_digest"))
RECEIPT_KEYS = frozenset(("protocol_version", "receipt_id", "project_id", "channel_id", "message_id", "recipient_session_id", "kind", "observed_message_sha256", "checkpoint_digest", "issued_at", "contract_digest"))
MAX_RELAY_BYTES = 64 * 1024  # bounded ingest; this is a local defensive ceiling, not a schema change


class ContractError(ValueError):
    """Wire fields or immutable identity failed validation."""


def identifier(value: Any, name: str = "identifier") -> str:
    if not isinstance(value, str) or ID.fullmatch(value) is None:
        raise ContractError(f"invalid {name}")
    return value


def digest(value: Any, name: str = "digest") -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise ContractError(f"invalid {name}")
    return value


def timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ContractError("invalid issued_at")
    # JSON Schema date-time is RFC3339, which requires explicit offset.
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("invalid issued_at") from exc
    if t.tzinfo is None or t.utcoffset() is None or "T" not in value:
        raise ContractError("issued_at needs timezone and T separator")
    return value


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for k, v in pairs:
        if k in obj:
            raise ContractError("duplicate JSON object key")
        obj[k] = v
    return obj


def _decode_canonical(body: bytes) -> dict[str, Any]:
    if not isinstance(body, bytes) or len(body) > MAX_RELAY_BYTES or not body:
        raise ContractError("invalid or oversized wire bytes")
    try:
        parsed = json.loads(body.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                            parse_constant=lambda _: (_ for _ in ()).throw(ContractError("invalid number")))
    except (UnicodeError, ValueError) as exc:
        raise ContractError("malformed relay JSON") from exc
    if not isinstance(parsed, dict) or canonical_bytes(parsed) != body:
        raise ContractError("non-canonical relay bytes")
    return parsed


def validate_message(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict) or set(message) != MESSAGE_KEYS:
        raise ContractError("relay message fields differ from frozen schema")
    if message["protocol_version"] != "1":
        raise ContractError("unsupported relay protocol")
    for key in ("message_id", "project_id", "channel_id", "to_session_id", "task_id", "canon_snapshot_id"):
        identifier(message[key], key)
    if message["from_session_id"] is not None:
        identifier(message["from_session_id"], "from_session_id")
    if type(message["lease_generation"]) is not int or message["lease_generation"] < 1:
        raise ContractError("invalid lease generation")
    if message["kind"] not in KINDS:
        raise ContractError("invalid relay kind")
    if message["kind"] == "HELLO" and (message["from_session_id"] is None or message["from_session_id"] != message["to_session_id"]):
        raise ContractError("HELLO must be self-addressed")
    timestamp(message["issued_at"])
    digest(message["payload_digest"], "payload_digest")
    digest(message["contract_digest"], "contract_digest")
    if not isinstance(message["payload_ref"], str) or REF.fullmatch(message["payload_ref"]) is None or message["payload_ref"][7:] != message["payload_digest"]:
        raise ContractError("payload reference/hash mismatch")
    return message


def parse_message(body: bytes) -> dict[str, Any]:
    return validate_message(_decode_canonical(body))


def validate_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        raise ContractError("relay receipt fields differ from frozen schema")
    if receipt["protocol_version"] != "1":
        raise ContractError("unsupported receipt protocol")
    for key in ("receipt_id", "project_id", "channel_id", "message_id", "recipient_session_id"):
        identifier(receipt[key], key)
    if receipt["kind"] not in RECEIPT_KINDS:
        raise ContractError("invalid receipt kind")
    digest(receipt["observed_message_sha256"], "observed_message_sha256")
    digest(receipt["contract_digest"], "contract_digest")
    if receipt["checkpoint_digest"] is not None:
        digest(receipt["checkpoint_digest"], "checkpoint_digest")
    timestamp(receipt["issued_at"])
    return receipt


def parse_receipt(body: bytes) -> dict[str, Any]:
    return validate_receipt(_decode_canonical(body))


def relay_path(root_path: str, recipient: str, message_id: str) -> str:
    identifier(recipient, "recipient")
    identifier(message_id, "message_id")
    root = validated_root(root_path)
    return f"{root}/mail/{recipient}/{message_id}.json"


def receipt_path(root_path: str, recipient: str, message_id: str, receipt_id: str) -> str:
    identifier(receipt_id, "receipt_id")
    return f"{validated_root(root_path)}/receipts/{identifier(recipient)}/{identifier(message_id)}/{receipt_id}.json"


def validated_root(root_path: str) -> str:
    if not isinstance(root_path, str):
        raise ContractError("relay root invalid")
    segments = root_path.split("/")
    if not 1 <= len(segments) <= 12 or any(not s or s in (".", "..") or ID.fullmatch(s) is None for s in segments):
        raise ContractError("relay root must be safe relative path")
    return root_path
