"""The source gate must catch source corruption, not just filenames."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('r9_ci_gate',ROOT/'infra/greenfield/ci_gate.py')
ci=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


def test_real_r9_source_passes_presence_and_syntax():
    assert ci.check(ROOT)==[]


def test_source_gate_rejects_a_real_corrupt_implementation(tmp_path):
    # Copy only tiny gate-relevant files; exercise failure without mutating the live tree.
    for item in ci.REQUIRED:
        src=ROOT/item;dst=tmp_path/item
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst)
    faulty=tmp_path/'src/nomad_studio/production/director/commands.py'
    faulty.write_text('def broken(:\n')
    errors=ci.check(tmp_path)
    assert any('invalid Python source:' in x for x in errors)


def test_source_gate_rejects_absent_frozen_contract(tmp_path):
    errors=ci.check(tmp_path)
    assert any('CONTRACTS/runtime_ports_v1.py' in x for x in errors)
