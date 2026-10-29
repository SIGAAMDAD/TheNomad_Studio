#!/usr/bin/env python3
"""R9 source-presence and syntax gate, intentionally not a runtime certification.

The first Studio release has no compatibility obligation to obsolete V1 facades.
All changed files and full runtime behavior are validated by separate test gates.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sqlite3
import sys

REQUIRED = (
    'CONTRACTS/runtime_ports_v1.py',
    'CONTRACTS/initial_schema_v1.sql',
    'CONTRACTS/schema_table_owners.json',
    'CONTRACTS/records.schema.json',
    'src/nomad_studio/contracts/runtime_ports_v1.py',
    'src/nomad_studio/production/director/api.py',
    'src/nomad_studio/production/director/commands.py',
    'src/nomad_studio/production/intake/api.py',
    'src/nomad_studio/production/contract/api.py',
    'src/nomad_studio/production/workgraph/api.py',
    'src/nomad_studio/production/repository/service.py',
    'src/nomad_studio/production/context/service.py',
    'src/nomad_studio/production/quality/api.py',
    'src/nomad_studio/trust/api.py',
    'src/nomad_studio/cabals/core/service.py',
    'src/nomad_studio/cabals/council/api.py',
    'src/nomad_studio/registry/api.py',
    'tests/r9_acceptance/test_sqlite_isolation.py',
    'tests/r9_acceptance/test_command_and_lifecycle_matrix.py',
    'tests/r9_acceptance/test_workgraph_properties.py',
    'tests/r9_acceptance/test_extended_adversarial.py',
    'tests/r9_acceptance/test_gate_fails_closed.py',
    'infra/greenfield/r9_raw_unit_gate.py',
)
SKIP = {'__pycache__','.pytest_cache','build','dist','.venv','.git'}


def check(root: Path) -> list[str]:
    issues=[]
    for name in REQUIRED:
        path=root/name
        if not path.is_file() or path.stat().st_size == 0:
            issues.append(f'missing R9 source or test: {name}')
    for subtree in ('src','tests','infra'):
        for path in sorted((root/subtree).rglob('*.py')):
            if set(path.relative_to(root).parts)&SKIP:
                continue
            try:
                ast.parse(path.read_bytes(),filename=str(path.relative_to(root)))
            except (SyntaxError,UnicodeDecodeError,ValueError) as exc:
                issues.append(f'invalid Python source: {path.relative_to(root)}: {exc}')
    if not all((root/p).is_file() for p in REQUIRED[:4]):
        return issues
    try:
        owners=json.loads((root/'CONTRACTS/schema_table_owners.json').read_text())['owner_per_table']
        conn=sqlite3.connect(':memory:')
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript((root/'CONTRACTS/initial_schema_v1.sql').read_text())
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'v1_%'")}
            if len(tables)!=56 or tables!=set(owners):
                issues.append('R9 initial schema/table owner registry mismatch')
            if conn.execute('PRAGMA foreign_keys').fetchone()[0]!=1 or list(conn.execute('PRAGMA foreign_key_check')):
                issues.append('SQLite foreign-key validation failed')
        finally:conn.close()
        schemas=json.loads((root/'CONTRACTS/records.schema.json').read_text())
        if not schemas.get('$defs'):
            issues.append('missing frozen R9 record schemas')
    except (OSError,ValueError,KeyError,sqlite3.Error) as exc:
        issues.append(f'R9 schema validation failed: {exc}')
    return issues


if __name__=='__main__':
    errors=check(Path(sys.argv[1] if len(sys.argv)>1 else '.').resolve())
    for error in errors:print(error,file=sys.stderr)
    if errors:sys.exit(1)
    print('R9 initial schema, code and adversarial test inputs valid; no runtime PASS implied')
