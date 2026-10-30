"""True API-server admission: fail closed until all 72 real handlers are wired."""
import json
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from studio.api import DomainRegistry, FrozenContracts, StudioOperatorService, ConfiguredOpaqueVerifier, VerifiedPrincipal
from studio.app.cli import configure_operator_server
from studio.app.composition import StudioComposition
from studio.app.errors import StudioError


class Projects:
    def granted_project_ids(self, _principal_id):
        return ()


def studio(service):
    return StudioComposition(projects=object(),releases=object(),recovery=object(),
                             runtime=object(),operator_service=service)


def test_unconfigured_operator_is_not_accepted_as_live_service():
    with pytest.raises(StudioError,match='operator API not composed'):
        configure_operator_server(studio(None))


def test_partial_registration_is_rejected_before_http_bind(monkeypatch):
    monkeypatch.setenv('STUDIO_OPERATOR_PORT','19999')
    contracts=FrozenContracts('CONTRACTS')
    registry=DomainRegistry()
    service=StudioOperatorService(contracts,object(),Projects(),registry,None)
    with pytest.raises(StudioError,match='72 unbound commands'):
        configure_operator_server(studio(service))


def test_unsafe_bind_address_is_refused_even_when_commands_are_available(monkeypatch):
    monkeypatch.setenv('STUDIO_OPERATOR_HOST','0.0.0.0')
    registry=DomainRegistry()
    domain=SimpleNamespace()
    for op in FrozenContracts('CONTRACTS').operations.values():
        setattr(domain,op.name,lambda *_a,**_kw:{})
    for owner in 'ABCDEFGHIJKLM':
        registry.register_owner(owner,domain)
    contracts=FrozenContracts('CONTRACTS')
    service=StudioOperatorService(contracts,object(),Projects(),registry,None)
    with pytest.raises(StudioError,match='loopback'):
        configure_operator_server(studio(service))


def test_complete_injected_http_service_starts_on_loopback(monkeypatch):
    # This tests the host binding, not correctness of the domain functions.
    monkeypatch.setenv('STUDIO_OPERATOR_PORT','1')
    monkeypatch.setenv('STUDIO_OPERATOR_HOST','127.0.0.1')
    registry=DomainRegistry()
    for owner in 'ABCDEFGHIJKLM':
        registry.register_owner(owner,SimpleNamespace())
    contracts=FrozenContracts('CONTRACTS')
    for operation in contracts.operations.values():
        setattr(registry._services[operation.owner],operation.name,lambda *_args,**_kwargs:{})
    service=StudioOperatorService(contracts,object(),Projects(),registry,None)
    # Use ephemeral port for a local host smoke test without privileged port access.
    monkeypatch.setenv('STUDIO_OPERATOR_PORT','12345')
    import studio.app.cli as cli
    original=cli.create_http_server
    def ephemeral(app,host,port):
        return original(app,host,0)
    monkeypatch.setattr(cli,'create_http_server',ephemeral)
    server=configure_operator_server(studio(service))
    thread=threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.server_address[0]=='127.0.0.1'
        assert server.server_address[1]>0
        # A listening socket is not a working operator service without
        # an authenticated request boundary. A missing token must fail closed.
        url=f'http://127.0.0.1:{server.server_address[1]}/v1/projects'
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url, timeout=3)
        assert error.value.code == 401
        payload=json.load(error.value)
        assert payload['code'] == 'forbidden'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
