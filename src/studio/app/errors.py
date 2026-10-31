"""Stable domain failure codes aligned with CONTRACTS/ports.pyi."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ErrorCode = Literal[
    "invalid", "missing", "forbidden", "conflict", "stale_revision",
    "stale_snapshot", "requires_owner", "capability_unavailable",
    "qa_failed", "provider_unavailable", "unknown_effect", "internal",
]


@dataclass(eq=False)
class StudioError(Exception):
    code: ErrorCode
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
