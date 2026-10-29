"""Mutate disposable inputs to ensure release gates cannot report false-green."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('r9_raw_unit_gate',ROOT/'infra/greenfield/r9_raw_unit_gate.py')
gate=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def test_syntax_mutation_fails_closed(tmp_path):
    source=tmp_path/'src'
    source.mkdir()
    for i in range(80):
        (source/f'module{i}.py').write_text('ANSWER = 1\n')
    assert gate.check_python_syntax(tmp_path)['python_files']==80
    (source/'module24.py').write_text('def invalid(:\n')
    with pytest.raises(RuntimeError,match='Python syntax failed'):
        gate.check_python_syntax(tmp_path)


def test_schema_mutation_fails_closed(tmp_path):
    c=tmp_path/'CONTRACTS'
    c.mkdir()
    for name in ('initial_schema_v1.sql','schema_table_owners.json','records.schema.json'):
        (c/name).write_bytes((ROOT/'CONTRACTS'/name).read_bytes())
    assert gate.check_schema(tmp_path)['tables']==56
    owners=c/'schema_table_owners.json'
    owners.write_text(owners.read_text().replace('"v1_projects": "CAB-B"','"v1_projects": "CAB-J"').replace('"v1_source_commits": "CAB-I",',''))
    with pytest.raises(RuntimeError,match='mismatch'):
        gate.check_schema(tmp_path)


def test_fk_enforced_and_disabled_flag_does_not_count_as_proof():
    sql=(ROOT/'CONTRACTS/initial_schema_v1.sql').read_text()
    with sqlite3.connect(':memory:') as db:
        db.executescript(sql)
        db.execute('PRAGMA foreign_keys=ON')
        assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert not list(db.execute('PRAGMA foreign_key_check'))
        db.execute('PRAGMA foreign_keys=OFF')
        assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 0
