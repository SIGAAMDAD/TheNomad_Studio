"""GitHub cooldown is per credential and distinguishes 403 denial from throttling."""
import pytest

from studio.integrations.common import ProviderFailure
from studio.integrations.rate_governor import GitHubRateGovernor


def rig():
    now = [1000.0]
    gate = GitHubRateGovernor(clock=lambda: now[0], utc_clock=lambda: 1_800_000_000)
    return gate, now


def test_numeric_retry_after_is_account_scoped_and_nonblocking():
    gate, now = rig()
    gate.observe('alice', {'Retry-After': '12'}, 429)
    gate.require_ready('bob')
    with pytest.raises(ProviderFailure) as exc:
        gate.require_ready('alice')
    assert exc.value.retry_after == 12
    now[0] += 13
    gate.require_ready('alice')


def test_unauthorized_403_does_not_throttle():
    gate, _ = rig()
    gate.observe('alice', {}, 403, b'{"message":"Resource not accessible"}')
    gate.require_ready('alice')


def test_secondary_403_message_without_headers_is_bounded():
    gate, now = rig()
    gate.observe('alice', {}, 403, b'{"message":"You have exceeded a secondary rate limit."}')
    with pytest.raises(ProviderFailure) as exc:
        gate.require_ready('alice')
    assert exc.value.retry_after == 60.0
    now[0] += 61
    gate.require_ready('alice')


def test_primary_zero_remaining_uses_reset_epoch():
    gate, now = rig()
    gate.observe('alice', {'X-RateLimit-Remaining': '0',
                           'X-RateLimit-Reset': '1800000040'}, 200)
    with pytest.raises(ProviderFailure) as exc:
        gate.require_ready('alice')
    assert exc.value.retry_after == 40
    now[0] += 41
    gate.require_ready('alice')


def test_invalid_http_date_stays_bounded_and_does_not_throw():
    gate, now = rig()
    gate.observe('alice', {'Retry-After': 'nonsense'}, 429)
    with pytest.raises(ProviderFailure) as exc:
        gate.require_ready('alice')
    assert exc.value.retry_after == 60
    now[0] += 61
    gate.require_ready('alice')


def test_cooldown_does_not_shorten_on_another_limit_event():
    gate, now = rig()
    gate.observe('alice', {'Retry-After': '40'}, 429)
    gate.observe('alice', {'Retry-After': '2'}, 429)
    with pytest.raises(ProviderFailure) as exc:
        gate.require_ready('alice')
    assert exc.value.retry_after == 40
