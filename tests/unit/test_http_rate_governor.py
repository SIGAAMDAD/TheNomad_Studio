"""Production HTTP uses the same credential governor for all provider instances."""
import pytest

from studio.integrations.common import ProviderFailure
from studio.integrations.http import JsonProvider
from studio.integrations.rate_governor import GitHubRateGovernor


class HTTP:
    def __init__(self):
        self.responses = []
        self.calls = []

    def request(self, method, url, headers, body=None):
        self.calls.append((method, url))
        return self.responses.pop(0)


def test_secondary_rate_limit_blocks_same_credential_but_not_another():
    current = [0.0]
    gate = GitHubRateGovernor(clock=lambda: current[0],
                              utc_clock=lambda: 1_800_000_000)
    http = HTTP()
    http.responses = [(429, {"Retry-After": "18"}, b'{"message":"limited"}'),
                      (200, {}, b'{"ok":true}'), (200, {}, b'{"ok":true}')]
    alice = JsonProvider(http, lambda: 'token', 'https://api.github.com',
                         rate_governor=gate, account_scope='alice')
    bob = JsonProvider(http, lambda: 'token', 'https://api.github.com',
                       rate_governor=gate, account_scope='bob')
    with pytest.raises(ProviderFailure):
        alice.request('GET', '/user')
    with pytest.raises(ProviderFailure) as exc:
        alice.request('GET', '/user')
    assert exc.value.retry_after == 18
    assert len(http.calls) == 1
    assert bob.request('GET', '/user') == {'ok': True}
    current[0] += 19
    assert alice.request('GET', '/user') == {'ok': True}
    assert len(http.calls) == 3


def test_plain_permission_403_is_not_misclassified_as_a_rate_limit():
    current = [0.0]
    gate = GitHubRateGovernor(clock=lambda: current[0],
                              utc_clock=lambda: 1_800_000_000)
    http = HTTP()
    http.responses = [(403, {}, b'{"message":"Resource not accessible"}'),
                      (200, {}, b'{"ok":true}')]
    api = JsonProvider(http, lambda: 'token', 'https://api.github.com',
                       rate_governor=gate, account_scope='alice')
    with pytest.raises(ProviderFailure) as exc:
        api.request('GET', '/user')
    assert exc.value.status == 403
    assert api.request('GET', '/user') == {'ok': True}


def test_rate_governor_without_account_identity_is_rejected():
    with pytest.raises(ValueError):
        JsonProvider(HTTP(), lambda:'token', 'https://api.github.com',
                     rate_governor=GitHubRateGovernor())
