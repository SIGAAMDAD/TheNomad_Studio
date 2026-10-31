"""Credential-scoped cooldown: invalid configuration, HTTP dates and malformed hints."""
from datetime import datetime, timezone
from email.utils import format_datetime

import pytest

from studio.integrations.rate_governor import GitHubRateGovernor
from studio.integrations.common import ProviderFailure


@pytest.mark.parametrize('kwargs',[
    {'clock':None},{'utc_clock':None},{'max_delay_seconds':True},
    {'max_delay_seconds':float('nan')},{'max_delay_seconds':0},
    {'max_delay_seconds':86401},
])
def test_invalid_governor_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        GitHubRateGovernor(**kwargs)


def test_scope_must_be_present_for_gate_and_response_observation():
    gate=GitHubRateGovernor()
    with pytest.raises(ValueError):
        gate.require_ready('')
    with pytest.raises(ValueError):
        gate.observe('',{},429)


@pytest.mark.parametrize('content',[
    b'not-json',b'{"message":1}',b'[]',b'{"message":"forbidden"}',
])
def test_non_rate_limit_responses_do_not_lock_account(content):
    gate=GitHubRateGovernor(clock=lambda:0)
    gate.observe('account',{},403,content)
    gate.require_ready('account')


def test_secondary_403_date_retry_after_and_expired_date():
    now=1_800_000_000.0
    gate=GitHubRateGovernor(clock=lambda:10.0,utc_clock=lambda:now)
    future=format_datetime(datetime.fromtimestamp(now+45,timezone.utc))
    gate.observe('account',{'Retry-After':future},403,b'{"message":"secondary rate limit"}')
    with pytest.raises(ProviderFailure) as exc:
        gate.require_ready('account')
    assert exc.value.retry_after==45

    # Expired HTTP-date must not introduce a negative or permanent cooldown.
    past=format_datetime(datetime.fromtimestamp(now-60,timezone.utc))
    another=GitHubRateGovernor(clock=lambda:0,utc_clock=lambda:now)
    another.observe('account',{'Retry-After':past},429)
    another.require_ready('account')


def test_invalid_reset_header_is_bounded_with_fallback_and_maximum():
    gate=GitHubRateGovernor(clock=lambda:5,utc_clock=lambda:1_800_000_000,max_delay_seconds=20)
    gate.observe('account',{'Retry-After':'nonsense','X-RateLimit-Reset':'invalid'},429)
    with pytest.raises(ProviderFailure) as exc:
        gate.require_ready('account')
    assert exc.value.retry_after==20


def test_primary_quota_reset_works_without_secondary_status():
    gate=GitHubRateGovernor(clock=lambda:10,utc_clock=lambda:1_800_000_000)
    gate.observe('account',{'X-RateLimit-Remaining':'0','X-RateLimit-Reset':'1800000009'},200)
    with pytest.raises(ProviderFailure) as exc:
        gate.require_ready('account')
    assert exc.value.retry_after==9