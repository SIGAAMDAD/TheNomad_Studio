"""Exercise the actual compiled .NET ToolHost on private pipes, no mock host."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from studio.app.toolhost_broker import PrivateToolHostBroker, ToolHostTransportError


ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "host" / "Nomad.ToolHost" / "bin" / "Release" / "net10.0" / "Nomad.ToolHost.dll"


def _inputs(tmp_path):
    launcher = shutil.which("dotnet")
    if not launcher or not HOST.is_file():
        pytest.skip("compiled .NET ToolHost unavailable in this test matrix")
    registry = tmp_path / "registry.json"
    registry.write_bytes(json.dumps({
        "version": 1, "packages": [], "grants": [],
        "workspaces": [], "operations": []
    }, separators=(",", ":")).encode("utf-8"))
    return {
        "dotnet": Path(launcher).resolve(), "host_dll": HOST,
        "host_sha256": hashlib.sha256(HOST.read_bytes()).hexdigest(),
        "registry": registry,
        "registry_sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
        "journal": tmp_path / "private-journal",
    }


def test_real_host_handshake_and_missing_operation(tmp_path):
    fields = _inputs(tmp_path)
    requests = []
    def authorize(frame):
        requests.append(dict(frame))
        return frame.get("kind") == "observe" and frame.get("project_id") == "project-a"

    with PrivateToolHostBroker(**fields, authorize=authorize) as broker:
        assert broker.running
        assert isinstance(broker.supported_isolation, tuple)
        request = {
            "kind": "observe", "protocol_version": "1.0.0",
            "project_id": "project-a", "operation_id": "not-registered",
        }
        broker.send(request)
        result = broker.receive()
        assert result["kind"] == "receipt"
        assert result["project_id"] == "project-a"
        assert result["operation_id"] == "not-registered"
        assert result["state"] == "failed"
        assert result["reason"] == "operation_not_found"
        with pytest.raises(ToolHostTransportError, match="denied"):
            broker.send({**request, "project_id": "other"})
        with pytest.raises(ToolHostTransportError, match="requires pinned sandbox"):
            broker.send({**request, "kind": "run"})
    assert requests == [request, {**request, "project_id": "other"}]


def test_bad_host_or_registry_sha_rejected_before_process_launch(tmp_path):
    fields = _inputs(tmp_path)
    with pytest.raises(ToolHostTransportError, match="bytes changed"):
        PrivateToolHostBroker(**{**fields, "registry_sha256": "0" * 64},
                              authorize=lambda _: True).start()
    with pytest.raises(ToolHostTransportError, match="bytes changed"):
        PrivateToolHostBroker(**{**fields, "host_sha256": "0" * 64},
                              authorize=lambda _: True).start()


def test_required_sandbox_fails_closed_before_any_command(tmp_path):
    fields = _inputs(tmp_path)
    with pytest.raises(ToolHostTransportError, match="required host sandbox"):
        with PrivateToolHostBroker(**fields, authorize=lambda _: True,
                                   expected_isolation="unavailable-sandbox"):
            pass


def test_trusted_authorizer_mandatory(tmp_path):
    fields = _inputs(tmp_path)
    with pytest.raises(ToolHostTransportError, match="authorizer"):
        PrivateToolHostBroker(**fields, authorize=None)
