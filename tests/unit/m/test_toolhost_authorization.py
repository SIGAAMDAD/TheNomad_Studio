"""Current ToolHost authorization is C-owned and denies stale grants/leases."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from studio.app.toolhost_authorization import CurrentToolHostAuthorizer, ToolHostBrokerBuilder
from studio.storage.workforce import WorkforceStorage


def setup_gate(row, lease=None):
    writer = WorkforceStorage(sqlite3.connect(":memory:"))
    class Tx:
        def __init__(self):
            self.calls = []
        def one(self, statement, args):
            self.calls.append((statement, args))
            return row if len(self.calls) == 1 else lease
    tx = Tx()
    writer.read = lambda project, callback: callback(tx)
    return writer, CurrentToolHostAuthorizer(writer), tx


def sample():
    row = dict(operation_state="prepared", shift_id="s", lease_generation=2,
               entrypoint_id="entry", grant_digest="g"*64,
               package_digest="p"*64, workspace_id="ws", revoked=0, enabled=1,
               shift_state="active", worker_id="w")
    frame = dict(kind="run", project_id="project", operation_id="op",
                 lease_generation=2, shift_id="s", entrypoint_id="entry",
                 grant_digest="g"*64, package_digest="p"*64, workspace_id="ws",
                 worker_id="w", task_id="task", source_revision_id="rev",
                 canon_snapshot_id="snap")
    return row, frame


def test_exact_current_grant_and_live_lease_authorize(tmp_path):
    row, frame = sample()
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    writer, authorize, tx = setup_gate(row, {"expires_at": expiry})
    try:
        assert authorize(frame)
        assert tx.calls[1][1] == ("rev", "project", "task", "s", "w", 2, "snap")
    finally:
        writer.close()


@pytest.mark.parametrize("field,value", [
    ("revoked", 1), ("enabled", 0), ("shift_state", "awaiting_hello"),
    ("operation_state", "observed"), ("lease_generation", 1),
    ("entrypoint_id", "foreign"), ("grant_digest", "invalid"),
    ("package_digest", "foreign"), ("workspace_id", "other"), ("worker_id", "other"),
])
def test_invalid_grant_state_and_operation_context_denied(field, value):
    row, frame = sample()
    row[field] = value
    writer, authorize, _ = setup_gate(row, {"expires_at": "2099-01-01T00:00:00+00:00"})
    try:
        assert not authorize(frame)
    finally:
        writer.close()


def test_expired_or_missing_lease_denied():
    row, frame = sample()
    for lease in (None, {"expires_at": "2000-01-01T00:00:00+00:00"},
                  {"expires_at": "bad timestamp"}):
        writer, authorize, _ = setup_gate(row, lease)
        try:
            assert not authorize(frame)
        finally:
            writer.close()


def test_authorized_observation_and_stale_cancel_denied():
    row, frame = sample()
    writer, authorize, _ = setup_gate(row)
    try:
        assert authorize({"kind":"observe", "project_id":"project", "operation_id":"op"})
    finally:
        writer.close()
    writer, authorize, _ = setup_gate(row)
    try:
        assert not authorize({"kind":"cancel", "project_id":"project",
                              "operation_id":"op", "lease_generation":1})
    finally:
        writer.close()


def test_broker_builder_constructs_authorizer_bound_to_one_c_writer(tmp_path):
    writer = WorkforceStorage(sqlite3.connect(":memory:"))
    builder = ToolHostBrokerBuilder(
        dotnet=tmp_path/"dotnet", host_dll=tmp_path/"host.dll",
        host_sha256="a"*64, registry=tmp_path/"registry",
        registry_sha256="b"*64, journal=tmp_path/"journal",
        expected_isolation="linux-bwrap-userns-rlimits-no-network")
    try:
        broker = builder.for_writer(writer, tmp_path)
        assert isinstance(broker.authorize, CurrentToolHostAuthorizer)
        assert broker.authorize.writer is writer
    finally:
        writer.close()
