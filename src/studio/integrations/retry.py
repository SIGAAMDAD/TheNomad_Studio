"""Deterministic non-blocking provider backoff, to be scheduled by worker G poller.

No waiting inside a browser chat and no blind re-dispatch of external writes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: float = 1.0
    cap_seconds: float = 120.0

    def delay(self, *, attempt: int, stable_key: str, retry_after: float | None = None) -> float:
        if attempt < 0 or not stable_key:
            raise ValueError("invalid retry attempt/key")
        if retry_after is not None:
            return max(0.0, min(self.cap_seconds, retry_after))
        # stable bounded jitter prevents synchronized project pollers without extra state
        bucket = int.from_bytes(hashlib.sha256(stable_key.encode()).digest()[:2], 'big') / 65535
        return min(self.cap_seconds, self.base_seconds * (2 ** min(attempt, 16)) * (0.75 + bucket * 0.5))
