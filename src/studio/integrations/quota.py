"""Pure GitHub quota observations for the existing relay connector.

Reports only observed values; never assumes student GitHub Pro raises REST limits.
This module does not initiate API requests, payments, worker executions or sleeps.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

class QuotaError(ValueError):
    pass

@dataclass(frozen=True)
class QuotaObservation:
    account_scope: str
    limit: int
    remaining: int
    reset_epoch: int
    observed_at: datetime
    secondary_retry_after: int | None

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0 or self.secondary_retry_after is not None

    def next_allowed_epoch(self, *, now_epoch: int) -> int:
        if self.secondary_retry_after is not None:
            return now_epoch + self.secondary_retry_after
        return self.reset_epoch if self.remaining == 0 else now_epoch


def parse_github_quota(headers: Mapping[str,str], *, account_scope: str,
                       observed_at: datetime, status_code: int) -> QuotaObservation:
    if not account_scope or observed_at.tzinfo is None:
        raise QuotaError('account scope and timezone-aware observation required')
    normalized={k.lower():v for k,v in headers.items()}
    def unsigned(key: str) -> int:
        value=normalized.get(key)
        if not isinstance(value,str) or not value.isdecimal():
            raise QuotaError(f'unknown or invalid GitHub {key}')
        return int(value)
    limit=unsigned('x-ratelimit-limit')
    remaining=unsigned('x-ratelimit-remaining')
    reset=unsigned('x-ratelimit-reset')
    if limit<=0 or remaining>limit:
        raise QuotaError('invalid GitHub quota pair')
    retry=None
    if status_code in (403,429):
        raw=normalized.get('retry-after')
        if raw is not None:
            if not raw.isdecimal():
                raise QuotaError('unsupported Retry-After; reconcile instead of guessing')
            retry=int(raw)
        elif remaining>0:
            # Secondary limit with no Retry-After is unknown, not an OK response.
            raise QuotaError('secondary rate limit without trusted retry schedule')
    elif status_code>=400:
        raise QuotaError('failed API call is not proof of available capacity')
    return QuotaObservation(account_scope,limit,remaining,reset,observed_at,retry)
