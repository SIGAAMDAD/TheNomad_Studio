"""Cover the private pipe transport's complete fail-closed state machine.

These isolated process fixtures test transport mechanics; the separate H job
tests the identical public broker API against the real compiled .NET host.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess

import pytest

from studio.app import toolhost_broker as broker


def _challenge(**updates):
    result = {"kind": "handshake", "protocol_version": "1.0.0",
              "host_nonce": "a" * 64, "supported_isolation": ["linux-isolated"]}
    return {**result, **updates}


def _receipt():
    return {"kind": "receipt", "protocol_version": "1.0.0",
            "project_id": "project", "operation_id": "op", "state": "failed",
            "lease_generation": 1, "host_identity": "test-host",
            "log_digests": [], "output_artifact_digests": []}


def _frame(value):
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


class FakeProcess:
    def __init__(self, payload):
        self.read_fd, write_fd = os.pipe()
        os.write(write_fd, payload)
        os.close(write_fd)
        self.stdout = os.fdopen(self.read_fd, "rb", buffering=0)
        self.stdin = io.BytesIO()
        self.killed = False
        self.waited = False

    def poll(self):
        return None if not self.waited else 0

    def wait(self, timeout):
        self.waited = True
        return 0

    def kill(self):
        self.killed = True


def configured(tmp_path, monkeypatch, *, challenge=None, receipt=None, authorize=None):
    dotnet = tmp_path / "dotnet"
    host = tmp_path / "host.dll"
    registry = tmp_path / "registry.json"
    for p in (dotnet, host, registry):
        p.write_bytes(b"original pinned data")
    payload = _frame(_challenge() if challenge is None else challenge)
    if receipt is not None:
        payload += _frame(receipt)
    fake = FakeProcess(payload)
    invocations = []

    def launch(args, **kwargs):
        invocations.append((args, kwargs))
        return fake
    monkeypatch.setattr(broker.subprocess, "Popen", launch)
    controller = broker.PrivateToolHostBroker(
        dotnet=dotnet, host_dll=host,
        host_sha256=hashlib.sha256(host.read_bytes()).hexdigest(),
        registry=registry,
        registry_sha256=hashlib.sha256(registry.read_bytes()).hexdigest(),
        journal=tmp_path / "private",
        authorize=(lambda _: True) if authorize is None else authorize,
        expected_isolation="linux-isolated", timeout=1)
    return controller, fake, invocations


def test_realistic_handshake_authorize_pipe_and_reject_unknown_operation(tmp_path, monkeypatch):
    controller, fake, invocations = configured(
        tmp_path, monkeypatch, receipt=_receipt(),
        authorize=lambda frame: frame["project_id"] == "project")
    with controller as connection:
        assert connection.running
        assert connection.supported_isolation == ("linux-isolated",)
        message = {"kind": "observe", "protocol_version": "1.0.0",
                   "project_id": "project", "operation_id": "op"}
        connection.send(message)
        assert connection.receive()["operation_id"] == "op"
        with pytest.raises(broker.ToolHostTransportError, match="denied"):
            connection.send({**message, "project_id": "another"})
        with pytest.raises(broker.ToolHostTransportError, match="unsupported"):
            connection.send({**message, "kind": "raw"})
        with pytest.raises(broker.ToolHostTransportError, match="protocol"):
            connection.send({**message, "protocol_version": "broken"})
        with pytest.raises(broker.ToolHostTransportError, match="unscoped"):
            connection.send({**message, "operation_id": ""})
        assert invocations[0][0][2] == "--registry"
        assert b"\"kind\":\"observe\"" in fake.stdin.getvalue()
    assert fake.waited
    assert not connection.running


def test_preflight_bad_hash_symlink_missing_and_invalid_authority(tmp_path, monkeypatch):
    controller, _, starts = configured(tmp_path, monkeypatch)
    controller.registry.write_bytes(b"mutated")
    with pytest.raises(broker.ToolHostTransportError, match="changed"):
        controller.start()
    assert starts == []
    controller.registry.write_bytes(b"original pinned data")
    controller.host_dll.unlink()
    controller.host_dll.symlink_to(controller.registry)
    with pytest.raises(broker.ToolHostTransportError, match="symlink"):
        controller.start()
    controller.host_dll.unlink()
    with pytest.raises(broker.ToolHostTransportError, match="missing"):
        controller.start()
    with pytest.raises(broker.ToolHostTransportError, match="authorizer"):
        broker.PrivateToolHostBroker(
            dotnet=controller.dotnet, host_dll=controller.host_dll,
            host_sha256=controller.host_sha256, registry=controller.registry,
            registry_sha256=controller.registry_sha256, journal=controller.journal,
            authorize=None)
    with pytest.raises(broker.ToolHostTransportError, match="timeout"):
        broker.PrivateToolHostBroker(
            dotnet=controller.dotnet, host_dll=controller.host_dll,
            host_sha256=controller.host_sha256, registry=controller.registry,
            registry_sha256=controller.registry_sha256, journal=controller.journal,
            authorize=lambda _: True, timeout=0)


@pytest.mark.parametrize("invalid", [
    _challenge(host_nonce="not-random"),
    _challenge(protocol_version="2.0"),
    _challenge(supported_isolation=["", "linux-isolated"]),
    _challenge(supported_isolation=[27]),
    {"kind": "handshake", "protocol_version": "1.0.0"},
])
def test_handshake_rejects_wrong_proof_or_schema(tmp_path, monkeypatch, invalid):
    controller, fake, _ = configured(tmp_path, monkeypatch, challenge=invalid)
    with pytest.raises(broker.ToolHostTransportError, match="handshake"):
        controller.start()
    assert fake.waited


def test_handshake_required_isolation_not_advertised(tmp_path, monkeypatch):
    controller, fake, _ = configured(
        tmp_path, monkeypatch, challenge=_challenge(supported_isolation=[]))
    with pytest.raises(broker.ToolHostTransportError, match="sandbox"):
        controller.start()
    assert fake.waited


def test_unbound_and_malformed_receipts_fail_closed(tmp_path, monkeypatch):
    controller, _, _ = configured(
        tmp_path, monkeypatch,
        receipt={**_receipt(), "operation_id": "forged"})
    with controller as live:
        live.send({"kind": "observe", "protocol_version": "1.0.0",
                   "project_id": "project", "operation_id": "op"})
        with pytest.raises(broker.ToolHostTransportError, match="unbound"):
            live.receive()


def test_receive_unexpected_eof_and_unstarted_transport(tmp_path, monkeypatch):
    controller, _, _ = configured(tmp_path, monkeypatch)
    with pytest.raises(broker.ToolHostTransportError, match="not started"):
        controller.receive()
    with controller as live:
        with pytest.raises(broker.ToolHostTransportError, match="closed"):
            live.receive()


def test_run_requires_explicit_sandbox_and_valid_current_authority(tmp_path, monkeypatch):
    controller, _, _ = configured(tmp_path, monkeypatch)
    controller.expected_isolation = None
    with controller as live:
        with pytest.raises(broker.ToolHostTransportError, match="pinned sandbox"):
            live.send({"kind": "run", "protocol_version": "1.0.0",
                       "project_id": "project", "operation_id": "op"})
    controller, _, _ = configured(
        tmp_path, monkeypatch, authorize=lambda _: (_ for _ in ()).throw(RuntimeError()))
    with controller as live:
        with pytest.raises(broker.ToolHostTransportError, match="unavailable"):
            live.send({"kind": "observe", "protocol_version": "1.0.0",
                       "project_id": "project", "operation_id": "op"})
