"""Regression checks for the production .NET coverage denominator."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import verify_studio_coverage as coverage


def _write_cobertura(path: Path, include_contracts: bool) -> None:
    classes = (
        '<class name="Nomad.ToolHost.HostRuntime" filename="HostRuntime.cs">'
        '<lines><line number="10" hits="1" /></lines></class>'
    )
    if include_contracts:
        classes += (
            '<class name="Nomad.ToolHost.Contracts.HostTypes" filename="HostTypes.cs">'
            '<lines><line number="20" hits="0" /></lines></class>'
        )
    path.write_text(
        '<coverage><packages><package name="Nomad.ToolHost"><classes>'
        + classes + '</classes></package></packages></coverage>',
        encoding='utf-8',
    )


def test_nested_host_source_must_be_instrumented(tmp_path, monkeypatch):
    host = tmp_path / 'host' / 'Nomad.ToolHost'
    (host / 'Contracts').mkdir(parents=True)
    (host / 'HostRuntime.cs').write_text('class HostRuntime {}')
    (host / 'Contracts' / 'HostTypes.cs').write_text('class HostTypes {}')
    # Compiler-generated files are not part of the production denominator.
    (host / 'obj').mkdir()
    (host / 'obj' / 'GeneratedAssemblyInfo.cs').write_text('// generated')
    report = tmp_path / 'coverage.xml'
    monkeypatch.setattr(coverage, 'ROOT', tmp_path)

    _write_cobertura(report, include_contracts=False)
    with pytest.raises(ValueError, match=r'HostTypes\\.cs'):
        coverage.host_coverage(report)

    _write_cobertura(report, include_contracts=True)
    assert coverage.host_coverage(report) == (1, 2)
