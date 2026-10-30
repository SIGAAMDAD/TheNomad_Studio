"""R9 process startup contract. Native providers in these tests are SIMULATED.

TLS listener and signal shutdown are real; signed external host receipts and
live agent execution are outside this local test's evidence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import socket
import ssl
import subprocess
import sys
import time

import pytest

from nomad_studio.app import _NATIVE_DEPENDENCIES, _load_runtime_factory, open_studio


def _config(tmp_path: Path) -> tuple[Path, Path, Path]:
    db = tmp_path / 'authority' / 'db.sqlite'
    cas = tmp_path / 'cas'
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'sqlite_path': str(db), 'cas_path': str(cas)}))
    return cfg, db, cas


def _env(tmp_path: Path) -> dict[str, str]:
    base = str(Path(__file__).resolve().parents[2] / 'src')
    return {**os.environ, 'PYTHONPATH': str(tmp_path) + os.pathsep + base,
            'PYTHONDONTWRITEBYTECODE': '1'}


def test_runtime_factory_input_is_operator_controlled_and_strict(tmp_path):
    cfg, db, cas = _config(tmp_path)
    for name in ('os.system', '__import__("os")', '../fake:create', 'x:y:z', 'bad module:factory'):
        with pytest.raises(ValueError):
            _load_runtime_factory(name)
    with open_studio(sqlite_path=db, cas_path=cas) as runtime:
        missing = set(runtime.health()['missing_native_dependencies'])
        assert missing == set(_NATIVE_DEPENDENCIES)
        assert {'native_host_journal', 'compliance_verifier', 'accepted_quality_outcomes'} <= missing


def test_missing_factory_never_starts_a_service_or_claims_ready(tmp_path):
    cfg, db, _ = _config(tmp_path)
    env = _env(tmp_path)
    env.pop('NOMAD_STUDIO_RUNTIME_FACTORY', None)
    proc = subprocess.run([sys.executable, '-m', 'nomad_studio.app', '--config', str(cfg)],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)['reason'] == 'NATIVE_RUNTIME_FACTORY_NOT_CONFIGURED'
    assert not db.exists()


def test_invalid_factory_and_mismatched_authority_fail_closed(tmp_path):
    cfg, db, cas = _config(tmp_path)
    (tmp_path / 'factory_wrong.py').write_text(
        'from nomad_studio.app import open_studio\n'
        'def create(*,sqlite_path,cas_path):\n'
        '    return open_studio(sqlite_path=sqlite_path+"-WRONG",cas_path=cas_path)\n')
    proc = subprocess.run([sys.executable, '-m', 'nomad_studio.app', '--config', str(cfg),
                           '--runtime-factory', 'factory_wrong:create'], cwd=tmp_path,
        env=_env(tmp_path), text=True, capture_output=True, timeout=20)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)['readiness'] == 'NOT_READY'
    assert not db.exists()
    # The failed factory must release its exclusive lock.
    with open_studio(sqlite_path=str(db) + '-WRONG', cas_path=cas) as runtime:
        assert runtime.health()['readiness'] == 'NOT_READY'


@pytest.mark.skipif(shutil.which('openssl') is None, reason='openssl is required for mTLS smoke')
def test_cli_operates_real_loopback_mtls_listener_and_restarts(tmp_path):
    """Positive *process* acceptance with simulated/native-provider fixtures."""
    cfg, db, cas = _config(tmp_path)
    cert = tmp_path / 'test-cert.pem'
    key = tmp_path / 'test-key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-nodes', '-newkey', 'rsa:2048',
        '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost',
        '-addext', 'basicConstraints=critical,CA:TRUE',
        '-addext', 'extendedKeyUsage=serverAuth,clientAuth'],
        capture_output=True, check=True, timeout=30)
    key.chmod(0o600)

    # A dynamically free loopback port is used only inside this test.
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]

    (tmp_path / 'simulated_native_bootstrap.py').write_text('''\
import os
import ssl
from nomad_studio.app import open_studio, _NATIVE_DEPENDENCIES
from nomad_studio.contracts.runtime_ports_v1 import Result
class SimulatedProviderReadiness:
    def check(self, provider_id):
        # SIMULATED local receipt, never evidence of live host capability.
        return Result('OK', 'OK', {'ready':True}, 'simulated-test-receipt')
def create(*, sqlite_path, cas_path):
    bindings={name:object() for name in _NATIVE_DEPENDENCIES}
    bindings['trust_anchors']={}
    bindings['native_event_delivery']=lambda _payload:Result('OK','OK',receipt_ref='simulated-delivery-receipt')
    def readback(mid,ref):
        row=runtime.state.read(lambda tx:tx.fetch_one('SELECT payload_digest FROM v1_outbox WHERE message_id=?',(mid,)))
        return {'status':'VERIFIED','message_id':mid,'receipt_ref':ref,'payload_digest':row['payload_digest']}
    bindings['native_outbox_readback']=readback
    bindings['deployment_readiness']=SimulatedProviderReadiness()
    bindings['provider_id']='test-local-only'
    runtime=open_studio(sqlite_path=sqlite_path, cas_path=cas_path, bindings=bindings)
    with runtime.state.capability('CAB-B').transaction() as tx:
        runtime.outbox.append(tx, {'domain':'production.director',
            'event_type':'FEATURE_RUN_TRANSITION', 'idempotency_key':'subprocess-smoke'})
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version=ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(os.environ['TEST_CERT'], os.environ['TEST_KEY'])
    tls.load_verify_locations(cafile=os.environ['TEST_CERT'])
    tls.verify_mode=ssl.CERT_REQUIRED
    runtime.start_gateway(tls_context=tls,certificate_identities={},
                          audit=lambda _record:None,port=int(os.environ['TEST_PORT']))
    return runtime
''')
    env = _env(tmp_path)
    env.update({'TEST_CERT': str(cert), 'TEST_KEY': str(key), 'TEST_PORT': str(port),
                'NOMAD_STUDIO_RUNTIME_FACTORY': 'simulated_native_bootstrap:create'})
    for _ in range(2):
        proc = subprocess.Popen([sys.executable, '-m', 'nomad_studio.app', '--config', str(cfg)],
            cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(cafile=str(cert))
            context.load_cert_chain(certfile=str(cert), keyfile=str(key))
            deadline = time.monotonic() + 10
            while True:
                if proc.poll() is not None:
                    out, err = proc.communicate(timeout=5)
                    pytest.fail(f'process failed to start: {proc.returncode}, {out[:500]}, {err[:500]}')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=1) as sock:
                        with context.wrap_socket(sock, server_hostname='localhost') as tls:
                            tls.sendall(b'POST /v1/owner/command HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
                            response = tls.recv(4096)
                            assert b'HTTP/1.1 400' in response, response
                    break
                except (ConnectionError, TimeoutError, ssl.SSLError):
                    if time.monotonic() >= deadline:
                        pytest.fail('mTLS gateway did not accept authenticated TLS')
                    time.sleep(0.05)
            # Running for a complete dispatcher polling period demonstrates
            # that this is not merely a startup-and-immediate-exit script.
            time.sleep(0.8)
            assert proc.poll() is None
            with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as sqlite:
                assert sqlite.execute('SELECT COUNT(*) FROM v1_outbox WHERE state=\'ACKED\'').fetchone()[0] == 1
                assert sqlite.execute('SELECT COUNT(*) FROM v1_outbox').fetchone()[0] == 1
        finally:
            proc.terminate()
            out, err = proc.communicate(timeout=10)
        assert proc.returncode == 0, (out, err)
        assert json.loads(out.splitlines()[0])['readiness'] == 'READY'
    assert db.is_file()
