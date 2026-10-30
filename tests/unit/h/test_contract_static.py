"""Offline source/contract checks. These are NOT a .NET compile or sandbox test."""
import os
import pathlib
import re
import json
import hashlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
HOST = ROOT / 'host' / 'Nomad.ToolHost'
CS = {p.name: p.read_text() for p in HOST.glob('*.cs')}
FROZEN_PATH = pathlib.Path(os.getenv('R5_PACKET_ROOT', '/absent')) / 'CONTRACTS'


def test_owned_paths_only():
    paths = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file()]
    assert paths, 'empty source tree'
    assert all(p.startswith('host/') or p.startswith('tests/unit/h/') for p in paths), paths


def test_generated_dto_is_frozen_copy():
    if not FROZEN_PATH.is_dir():
        pytest.skip('frozen contract packet unavailable')
    a = (HOST / 'Contracts' / 'HostTypes.cs').read_bytes()
    b = (FROZEN_PATH / 'host.types.cs').read_bytes()
    assert a == b
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()


def test_all_protocol_frame_types_supported():
    if not FROZEN_PATH.is_dir():
        pytest.skip('frozen contract packet unavailable')
    contract = json.loads((FROZEN_PATH / 'host_protocol.schema.json').read_text())
    schemas = contract['$defs']
    assert set(schemas) == {'Handshake', 'ToolRun', 'ToolObserve', 'ToolCancel', 'ToolReceipt'}
    for kind in ('handshake', 'run', 'observe', 'cancel'):
        assert f'["{kind}"]' in CS['Protocol.cs']
    for state in schemas['ToolReceipt']['properties']['state']['enum']:
        assert state in ''.join(CS.values())


def test_no_paid_model_execution():
    source = '\n'.join(CS.values())
    for forbidden in ('OpenAIClient(', 'AnthropicClient(', 'ChatCompletion(',
                      'HttpClient(', 'WebSocket(', 'TcpListener(', 'Azure.AI.OpenAI'):
        assert forbidden not in source


def test_process_argv_and_isolation_fail_closed():
    source = CS['Sandbox.cs']
    assert 'ArgumentList.Add' in source
    assert 'UseShellExecute = false' in source
    assert 'capability_unavailable' in source
    assert '--unshare-all' in source
    assert '--die-with-parent' in source
    assert '--clearenv' in source
    assert '--ro-bind' in source
    assert '--bind' in source
    assert 'LinuxPreflight' in source
    assert 'Process.Start(' not in '\n'.join(CS.values())


def test_operation_binding_and_idempotency():
    source = CS['HostConfig.cs']
    assert all(s in source for s in ('auth.ProjectId != run.ProjectId', 'auth.LeaseGeneration != run.LeaseGeneration',
        'auth.SourceRevisionId != run.SourceRevisionId', 'auth.CanonSnapshotId != run.CanonSnapshotId',
        'grant.PackageDigest != package.Digest', 'ValidateInstalledPackage', 'CheckNoLinks'))
    assert 'idempotency_conflict' in CS['HostRuntime.cs']
    assert 'stale_lease_generation' in CS['HostRuntime.cs']
    assert 'ReconcileInterrupted' in CS['ToolJournal.cs']


def test_no_unowned_ddl_or_external_api():
    for p in ROOT.rglob('*.sql'):
        raise AssertionError(f'WG-H wrote SQL outside ownership: {p}')
    assert 'namespace Nomad.Studio.ToolHost' in CS['HostRuntime.cs']
