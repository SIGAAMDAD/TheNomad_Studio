"""Version-pinned, style-only persona change proposals (never profile writes)."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Protocol

from .models import DevelopmentResult, digest_ok

# The Studio WG-AG registry remains the only owner. These are *proposal* keys,
# and must be reconciled to the actual private/Studio schema by the integrator.
STYLE_KEYS = frozenset({"skepticism", "exploration", "directness", "formality"})


def canonical_digest(*, worker_id: str, version: int, style: Mapping[str, int]) -> str:
    payload = {"worker_id": worker_id, "version": version, "style": dict(style)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                     separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class PersonaSnapshot:
    worker_id: str
    version: int
    digest: str
    style: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.worker_id or self.version < 1 or not digest_ok(self.digest):
            raise ValueError("invalid current persona identity/version/digest")
        if (not set(self.style).issubset(STYLE_KEYS) or
                any(type(v) is not int or not 0 <= v <= 100 for v in self.style.values())):
            raise ValueError("invalid typed persona style snapshot")
        if self.digest != canonical_digest(worker_id=self.worker_id,
                                            version=self.version, style=self.style):
            raise ValueError("persona snapshot digest not consistent")


class PersonaRegistryPort(Protocol):
    """Read-only WG-AG style projection; no write method or privilege grant."""
    def read_current(self, worker_id: str) -> PersonaSnapshot | None: ...


@dataclass(frozen=True)
class PersonaRevisionRequest:
    request_id: str
    worker_id: str
    current_version: int
    proposed_version: int
    current_digest: str
    proposed_digest: str
    style_delta: Mapping[str, int]
    reason_evidence_digest: str
    status: str = "OWNER_REVIEW_REQUIRED"
    # No authority grant or effect-side operation is possible from this DTO.


def propose_persona_revision(
    *, worker_id: str, expected_version: int, expected_digest: str,
    style_delta: Mapping[str, int], evidence_digest: str,
    registry: PersonaRegistryPort | None,
) -> DevelopmentResult:
    if registry is None:
        return DevelopmentResult("UNSUPPORTED", "current trusted persona registry unavailable")
    if (not worker_id or expected_version < 1 or not digest_ok(expected_digest) or
            not digest_ok(evidence_digest)):
        return DevelopmentResult("DENIED", "invalid revision or evidence parameters")
    if not style_delta or not set(style_delta).issubset(STYLE_KEYS):
        return DevelopmentResult("DENIED", "only explicitly bounded presentation keys may change")
    if any(type(value) is not int or not 0 <= value <= 100 for value in style_delta.values()):
        return DevelopmentResult("DENIED", "style settings must be bounded integers")
    try:
        current = registry.read_current(worker_id)
    except Exception:
        return DevelopmentResult("UNSUPPORTED", "persona registry read failed")
    if current is None:
        return DevelopmentResult("UNSUPPORTED", "authoritative persona snapshot unavailable")
    if current.worker_id != worker_id or current.version != expected_version or current.digest != expected_digest:
        return DevelopmentResult("DENIED", "stale or mismatched persona snapshot")
    if any(current.style.get(k) == v for k, v in style_delta.items()):
        return DevelopmentResult("DENIED", "no-op or mixed no-op style delta")
    new_style = dict(current.style)
    new_style.update(style_delta)
    new_version = current.version + 1
    proposed_digest = canonical_digest(worker_id=worker_id, version=new_version, style=new_style)
    payload = [worker_id, current.version, current.digest, proposed_digest,
               sorted(style_delta.items()), evidence_digest]
    request_id = "persona-request-" + hashlib.sha256(json.dumps(
        payload, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    request = PersonaRevisionRequest(
        request_id, worker_id, current.version, new_version, current.digest,
        proposed_digest, dict(style_delta), evidence_digest,
    )
    return DevelopmentResult("PROPOSED", "no persona write or owner approval performed", request)
