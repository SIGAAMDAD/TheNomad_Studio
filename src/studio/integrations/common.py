"""WG-G provider boundary: project scope, canonical bytes, and explicit uncertainty."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,95}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_SHA = re.compile(r"[a-f0-9]{40}\Z")


class IntegrationError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


class RemoteUnknown(IntegrationError):
    def __init__(self, detail: str):
        super().__init__("unknown_effect", detail)


class ProviderFailure(IntegrationError):
    def __init__(self, detail: str, status: int | None = None, retry_after: float | None = None):
        super().__init__("provider_unavailable", detail)
        self.status = status
        self.retry_after = retry_after


def identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or value in (".", ".."):
        raise IntegrationError("invalid", "invalid identifier")
    return value


def digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise IntegrationError("invalid", "invalid SHA-256")
    return value


def git_sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise IntegrationError("invalid", "invalid git SHA-1")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj: Mapping[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)).encode("utf-8")


def parse_canonical(raw: bytes, max_bytes: int = 1_048_576) -> dict[str, Any]:
    if len(raw) > max_bytes:
        raise IntegrationError("invalid", "JSON file exceeds limit")
    def unique(pairs):
        value = {}
        for k, v in pairs:
            if k in value:
                raise IntegrationError("invalid", "duplicate JSON key")
            value[k] = v
        return value
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise IntegrationError("invalid", "invalid JSON encoding") from exc
    if not isinstance(result, dict) or raw != canonical(result):
        raise IntegrationError("invalid", "JSON must be canonical UTF-8 with sorted keys")
    return result


def iso_datetime(s: str) -> None:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("no timezone")
    except (AttributeError, ValueError) as exc:
        raise IntegrationError("invalid", "invalid timestamp") from exc


@dataclass(frozen=True)
class ProjectBinding:
    """Issued by the calling trusted A/J service; does not establish authentication itself."""
    project_id: str
    provider_account_id: str

    def __post_init__(self):
        identifier(self.project_id)
        identifier(self.provider_account_id)

    def require(self, project_id: str) -> None:
        if self.project_id != project_id:
            raise IntegrationError("forbidden", "wrong project binding")
