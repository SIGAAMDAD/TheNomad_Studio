"""Strict remote relay envelope/receipt validation; identity remains weak GitHub provenance."""
from __future__ import annotations

from typing import Any
from .common import IntegrationError, identifier, digest, iso_datetime, parse_canonical, sha256

_MESSAGE = frozenset("protocol_version message_id project_id channel_id from_session_id to_session_id task_id lease_generation kind issued_at payload_digest payload_ref canon_snapshot_id contract_digest".split())
_RECEIPT = frozenset("protocol_version receipt_id project_id channel_id message_id recipient_session_id kind observed_message_sha256 checkpoint_digest issued_at contract_digest".split())
_KINDS = frozenset("HELLO ASSIGN NOTE QUESTION ANSWER EVIDENCE REVIEW HANDOFF STOP STATUS".split())
_RKINDS = frozenset("SEEN CHECKPOINT SUBMITTED STOP_SEEN".split())


def validate_relay(raw: bytes, *, project_id: str, recipient: str | None = None) -> tuple[dict[str, Any], str]:
    obj = parse_canonical(raw)
    if obj.keys() != _MESSAGE or obj["protocol_version"] != "1":
        raise IntegrationError("invalid", "relay envelope fields/version mismatch")
    for field in ("message_id", "project_id", "channel_id", "to_session_id", "task_id", "canon_snapshot_id"):
        identifier(obj[field])
    if obj["from_session_id"] is not None:
        identifier(obj["from_session_id"])
    for field in ("payload_digest", "contract_digest"):
        digest(obj[field])
    if obj["payload_ref"] != "sha256:" + obj["payload_digest"]:
        raise IntegrationError("invalid", "payload reference/digest mismatch")
    if obj["kind"] not in _KINDS or type(obj["lease_generation"]) is not int or obj["lease_generation"] < 1:
        raise IntegrationError("invalid", "relay kind/lease invalid")
    iso_datetime(obj["issued_at"])
    if obj["project_id"] != project_id or (recipient is not None and obj["to_session_id"] != recipient):
        raise IntegrationError("forbidden", "relay project/recipient mismatch")
    if obj["kind"] == "HELLO" and obj["from_session_id"] != obj["to_session_id"]:
        raise IntegrationError("forbidden", "HELLO must enroll only itself")
    return obj, sha256(raw)


def validate_receipt(raw: bytes, *, project_id: str, recipient: str | None = None) -> tuple[dict[str, Any], str]:
    obj = parse_canonical(raw)
    if obj.keys() != _RECEIPT or obj["protocol_version"] != "1":
        raise IntegrationError("invalid", "receipt fields/version mismatch")
    for field in ("receipt_id", "project_id", "channel_id", "message_id", "recipient_session_id"):
        identifier(obj[field])
    for field in ("observed_message_sha256", "contract_digest"):
        digest(obj[field])
    if obj["checkpoint_digest"] is not None:
        digest(obj["checkpoint_digest"])
    if obj["kind"] not in _RKINDS:
        raise IntegrationError("invalid", "receipt kind invalid")
    iso_datetime(obj["issued_at"])
    if obj["project_id"] != project_id or (recipient is not None and obj["recipient_session_id"] != recipient):
        raise IntegrationError("forbidden", "receipt project/recipient mismatch")
    return obj, sha256(raw)
