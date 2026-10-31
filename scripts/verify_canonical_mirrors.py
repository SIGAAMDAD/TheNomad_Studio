#!/usr/bin/env python3
"""Fail closed when frozen contract/standalone skill mirrors or role bytes drift.

Checks source bytes, never rewrites them. The 526 content-addressed resources
are production data; distributed skill files are required for standalone use.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN_PAIRS = (
    ('CONTRACTS/initial_schema.sql', 'schema/initial_schema.sql'),
    ('CONTRACTS/state_machines.json', 'schema/state_machines.json'),
    ('CONTRACTS/host.types.cs', 'host/Nomad.ToolHost/Contracts/HostTypes.cs'),
)
SKILL_REFERENCES = (
    'CHAIN_OF_COMMAND.md', 'DAILY_AUDIT.md', 'DEPARTMENT_CHAIN.md',
    'LEDGER_EVENTS.md', 'REVIEW_GATES.md', 'departments.v4.json',
    'ledger_event.schema.json',
)
ROLE_MANIFEST_SOURCE = 'src/studio/organization/catalog/source/roles_manifest.json'
DEPARTMENT_SOURCE = 'src/studio/organization/catalog/source/departments.v4.json'


def verify_pair(root: Path, source: str, mirror: str) -> None:
    a, b = root / source, root / mirror
    if not a.is_file() or not b.is_file() or a.read_bytes() != b.read_bytes():
        raise ValueError(f'canonical resource and required mirror differ: {source} vs {mirror}')


def verify_catalog(root: Path) -> None:
    base = root / 'src/studio/organization/catalog'
    index = json.loads((base / 'resource-index.json').read_text(encoding='utf-8'))
    if len(index) != 526 or len(set(index.values())) != 526:
        raise ValueError('catalog must resolve 526 distinct pinned resources')
    for content_sha256, name in index.items():
        source = base / name
        if not (len(content_sha256) == 64 and source.is_file() and
                hashlib.sha256(source.read_bytes()).hexdigest() == content_sha256):
            raise ValueError(f'catalog digest or resource missing: {name}')
    for kind, suffix, expected in (('manifests','.json',174),('resources/roles','.md',174),('resources/prompts','.md',174)):
        actual = {x.relative_to(base).as_posix() for x in (base / kind).glob('*'+suffix)}
        if len(actual) != expected or not actual.issubset(index.values()):
            raise ValueError(f'role catalog missing or extra {kind}: {len(actual)}')


def verify(root: Path = ROOT) -> None:
    for a, b in FROZEN_PAIRS:
        verify_pair(root, a, b)
    for name in SKILL_REFERENCES:
        canonical = f'studio/workforce/v4/{name}'
        for skill in ('studio-director', 'rpg-studio-all-roles'):
            verify_pair(root, canonical, f'skills/{skill}/references/{name}')
    verify_pair(root, ROLE_MANIFEST_SOURCE,
                'skills/rpg-studio-all-roles/references/roles_manifest.json')
    for item in ('studio/workforce/v4/departments.v4.json',
                 'skills/studio-director/references/departments.v4.json',
                 'skills/rpg-studio-all-roles/references/departments.v4.json'):
        verify_pair(root, DEPARTMENT_SOURCE, item)
    verify_catalog(root)


if __name__ == '__main__':
    verify()
    print('Canonical contracts, 174 role packs and 526 catalog resources verified')
