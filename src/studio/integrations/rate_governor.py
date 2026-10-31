"""Shared non-blocking GitHub API cooldown keyed by trusted credential identity.

The governor never sleeps, stores credentials, or replays a provider write. The
caller may retry a locally blocked read only after the observed delay. Unknown
mutations retain their durable exact-readback requirement in the outbox.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Callable, Mapping

from .common import ProviderFailure


class GitHubRateGovernor:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 utc_clock: Callable[[], float] = time.time,
                 max_delay_seconds: float = 3600.0):
        if (not callable(clock) or not callable(utc_clock) or
                type(max_delay_seconds) not in (int, float) or
                not math.isfinite(max_delay_seconds) or
                not 1 <= max_delay_seconds <= 86400):
            raise ValueError('invalid GitHub rate governor configuration')
        self._clock = clock
        self._utc_clock = utc_clock
        self._max_delay = float(max_delay_seconds)
        self._lock = Lock()
        self._not_before: dict[str, float] = {}

    def require_ready(self, account_scope: str) -> None:
        if not isinstance(account_scope, str) or not account_scope:
            raise ValueError('GitHub credential scope required')
        now = self._clock()
        with self._lock:
            remaining = self._not_before.get(account_scope, 0.0) - now
        if remaining > 0:
            raise ProviderFailure('GitHub credential scope is cooling down',
                                  status=429, retry_after=remaining)

    @staticmethod
    def _is_secondary_limit(body: bytes) -> bool:
        try:
            message = json.loads(body).get('message', '')
        except (TypeError, ValueError, AttributeError):
            return False
        if not isinstance(message, str):
            return False
        lowered = message.lower()
        return ('secondary rate limit' in lowered or
                'rate limit exceeded' in lowered or
                'abuse detection' in lowered)

    def observe(self, account_scope: str, headers: Mapping[str, str],
                status: int, body: bytes = b'') -> None:
        if not isinstance(account_scope, str) or not account_scope:
            raise ValueError('GitHub credential scope required')
        normalized = {k.lower(): v for k, v in headers.items()}
        raw_retry = normalized.get('retry-after')
        remaining = normalized.get('x-ratelimit-remaining')
        reset = normalized.get('x-ratelimit-reset')
        secondary = status == 429 or (status == 403 and (
            raw_retry is not None or remaining == '0' or
            self._is_secondary_limit(body)))
        depleted = remaining == '0'
        if not (secondary or depleted):
            return
        delay = None
        if raw_retry:
            try:
                parsed = float(raw_retry)
                if math.isfinite(parsed) and parsed >= 0:
                    delay = parsed
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw_retry)
                    if retry_at.tzinfo is not None:
                        delay = (retry_at - datetime.fromtimestamp(
                            self._utc_clock(), timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    pass
        if delay is None and reset is not None:
            try:
                reset_epoch = float(reset)
                if math.isfinite(reset_epoch):
                    delay = reset_epoch - self._utc_clock()
            except (ValueError, TypeError):
                pass
        if delay is None and secondary:
            delay = 60.0
        if delay is None:
            return
        delay = min(self._max_delay, max(0.0, delay))
        with self._lock:
            self._not_before[account_scope] = max(
                self._not_before.get(account_scope, 0.0), self._clock() + delay)
