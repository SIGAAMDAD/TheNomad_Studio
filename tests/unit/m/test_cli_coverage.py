"""Actual production CLI error and lifecycle paths, with external host mocked."""
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from studio.app import cli
from studio.app.errors import StudioError


def test_factory_rejects_bad_import_paths_and_wrong_return(monkeypatch):
    for path in ('', 'module', ':factory', 'module:', 'module:1invalid'):
        with pytest.raises(StudioError):
            cli.load_composition(path)
    fake = ModuleType('studio_fake_factory')
    fake.wrong = lambda: object()
    monkeypatch.setitem(sys.modules, 'studio_fake_factory', fake)
    with pytest.raises(StudioError, match='wrong type'):
        cli.load_composition('studio_fake_factory:wrong')


def test_factory_accepts_proper_composition(monkeypatch):
    class Composition: pass
    fake = ModuleType('studio_good_factory')
    expected = Composition()
    fake.produce = lambda: expected
    monkeypatch.setitem(sys.modules, 'studio_good_factory', fake)
    monkeypatch.setattr(cli,'StudioComposition',Composition)
    assert cli.load_composition('studio_good_factory:produce') is expected


class Domains:
    def __init__(self, missing=()):self.missing_operations=missing
    def missing(self, contracts):return self.missing_operations


class Service:
    def __init__(self, missing=()):self.domains=Domains(missing);self.contracts=object()


def test_server_requires_bound_operator_and_valid_host(monkeypatch):
    monkeypatch.setattr(cli,'StudioOperatorService',Service)
    with pytest.raises(StudioError,match='authenticated'):
        cli.configure_operator_server(SimpleNamespace(operator_service=None))
    with pytest.raises(StudioError,match='unbound'):
        cli.configure_operator_server(SimpleNamespace(operator_service=Service(['one','two'])))
    monkeypatch.setenv('STUDIO_OPERATOR_HOST','0.0.0.0')
    with pytest.raises(StudioError,match='loopback'):
        cli.configure_operator_server(SimpleNamespace(operator_service=Service()))


@pytest.mark.parametrize('port',('wrong','0','65536','-1'))
def test_server_rejects_invalid_port(monkeypatch,port):
    monkeypatch.setattr(cli,'StudioOperatorService',Service)
    monkeypatch.setenv('STUDIO_OPERATOR_HOST','127.0.0.1')
    monkeypatch.setenv('STUDIO_OPERATOR_PORT',port)
    with pytest.raises(StudioError,match='port|Port|HTTP'):
        cli.configure_operator_server(SimpleNamespace(operator_service=Service()))


def test_server_composes_webhook_mux_and_preserves_loopback(monkeypatch):
    monkeypatch.setattr(cli,'StudioOperatorService',Service)
    monkeypatch.setattr(cli,'StudioWSGI',lambda service: ('operator',service))
    monkeypatch.setattr(cli,'WebhookMuxWSGI',lambda operator,webhook:('mux',operator,webhook))
    connections=[]
    monkeypatch.setattr(cli,'create_http_server',lambda app,host,port:connections.append((app,host,port)) or 'server')
    monkeypatch.setenv('STUDIO_OPERATOR_PORT','8181')
    monkeypatch.setenv('STUDIO_OPERATOR_HOST','::1')
    service=Service()
    assert cli.configure_operator_server(SimpleNamespace(operator_service=service,webhook_wsgi='hook'))=='server'
    assert connections==[(('mux',('operator',service),'hook'),'::1',8181)]
    connections.clear()
    assert cli.configure_operator_server(SimpleNamespace(operator_service=service,webhook_wsgi=None))=='server'
    assert connections==[(('operator',service),'::1',8181)]


class Runtime:
    def __init__(self,fail=False): self.fail=fail;self.entries=0;self.exits=0
    def __enter__(self):
        self.entries+=1
        if self.fail: raise RuntimeError('runtime startup failure')
        return self
    def __exit__(self,*args):self.exits+=1


class FakeThread:
    def __init__(self,target,name,daemon):self.target=target;self.alive=False
    def start(self):self.alive=True;self.target()
    def is_alive(self):return self.alive
    def join(self,timeout):self.alive=False


class Server:
    def __init__(self,raises=False):self.raises=raises;self.closed=False;self.shutdown_calls=0
    def serve_forever(self):
        if self.raises:raise RuntimeError('server crash')
    def shutdown(self):self.shutdown_calls+=1
    def server_close(self):self.closed=True


class SignalEvent:
    def set(self):pass
    def wait(self):return True


def setup_main(monkeypatch,*,server_raises=False,runtime_fails=False):
    runtime=Runtime(fail=runtime_fails)
    server=Server(raises=server_raises)
    monkeypatch.setattr(cli,'load_composition',lambda path:SimpleNamespace(runtime=runtime))
    monkeypatch.setattr(cli,'configure_operator_server',lambda app:server)
    monkeypatch.setattr(cli.threading,'Event',SignalEvent)
    monkeypatch.setattr(cli.threading,'Thread',FakeThread)
    monkeypatch.setattr(cli.signal,'signal',lambda *a:None)
    return runtime,server


def test_main_happy_shutdown(monkeypatch):
    runtime,server=setup_main(monkeypatch)
    assert cli.main()==0
    assert runtime.entries==1 and runtime.exits==1
    assert server.closed and server.shutdown_calls==1


def test_main_records_server_thread_failure(monkeypatch):
    runtime,server=setup_main(monkeypatch,server_raises=True)
    assert cli.main()==1
    assert server.closed and server.shutdown_calls==1


def test_main_runtime_failure_still_closes_server(monkeypatch):
    runtime,server=setup_main(monkeypatch,runtime_fails=True)
    assert cli.main()==1
    assert server.closed


def test_main_configuration_failure(monkeypatch):
    monkeypatch.setattr(cli,'load_composition',lambda _: (_ for _ in ()).throw(StudioError('invalid','broken factory')))
    assert cli.main()==78
    monkeypatch.setattr(cli,'load_composition',lambda _: SimpleNamespace())
    monkeypatch.setattr(cli,'configure_operator_server',lambda _: (_ for _ in ()).throw(OSError('no socket')))
    assert cli.main()==78