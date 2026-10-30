"""Frozen-plan and path-ownership conformance checks; never mutate contracts."""
from __future__ import annotations

from pathlib import Path
from .core import digest, read_json, safe_relative

EXPECTED_PLAN_SHA = '8d68e255008369a299c66ca1a0c253e60faecb2ef46415ad09cbb9b38b41d09b'
WORKGROUPS = tuple(f'WG-{c}' for c in 'ABCDEFGHIJKLM')
FROZEN_PREFIXES = ('CONTRACTS/', 'ARCHITECTURE/', 'WORKERS/', 'REQUIREMENTS/', 'RELAY/')
FROZEN_NAMES = frozenset({'plan.json', 'manifest.json'})


class ContractError(ValueError):
    pass


def _paths_overlap(a: str, b: str) -> bool:
    a_root = a[:-2] if a.endswith('/**') else a
    b_root = b[:-2] if b.endswith('/**') else b
    if a == b:
        return True
    if a.endswith('/**') and b_root.startswith(a_root):
        return True
    if b.endswith('/**') and a_root.startswith(b_root):
        return True
    return False


def load_plan(packet: Path) -> dict:
    data = (packet / 'plan.json').read_bytes()
    actual = digest(data)
    if actual != EXPECTED_PLAN_SHA:
        raise ContractError(f'plan SHA differs: {actual} != {EXPECTED_PLAN_SHA}')
    plan = read_json(packet / 'plan.json')
    if set(w['id'] for w in plan['workgroups']) != set(WORKGROUPS):
        raise ContractError('workgroups must be exactly A-M')
    waves = plan.get('waves', [])
    if len(waves) != 1:
        raise ContractError('exactly one implementation wave required')
    if len(plan['workgroups']) != 13 or len({w['id'] for w in plan['workgroups']}) != 13:
        raise ContractError('duplicate or missing workgroup')
    if set(waves[0].get('groups', [])) != set(WORKGROUPS) or waves[0].get('integration_owner') != 'WG-M':
        raise ContractError('wave is missing workers or canonical integrator M')
    policy = plan['constraints']
    if (policy.get('expected_workgroups') != 13 or policy.get('expected_waves') != 1
        or policy.get('git_extra_workgroup_branches') != 0 or policy.get('ai_api_workers') != 0
        or policy.get('legacy_compatibility') is not False):
        raise ContractError('frozen no-extra-branches/no-agent/no-legacy constraints changed')
    execution = plan['execution_topology']
    if (execution.get('model') != 'manual_chatgpt_browser_github_relay'
            or execution.get('initiator') != 'human'
            or execution.get('paid_llm_api_execution') is not False):
        raise ContractError('manual human-started chat model changed')
    all_owners = owners(plan)
    for i, left in enumerate(WORKGROUPS):
        for right in WORKGROUPS[i+1:]:
            for lpat in all_owners[left]:
                for rpat in all_owners[right]:
                    if _paths_overlap(lpat, rpat):
                        raise ContractError(f'workgroup path ownership overlap: {left}/{right}: {lpat}/{rpat}')
    return plan


def owners(plan: dict) -> dict[str, list[str]]:
    return {w['id']: list(w['owned_paths']) for w in plan['workgroups']}


def path_owned(rel: str, patterns: list[str]) -> bool:
    safe_relative(rel)
    for pattern in patterns:
        if pattern.endswith('/**') and rel.startswith(pattern[:-2]):
            return True
        if pattern == rel:
            return True
    return False


def assert_owned(rel: str, worker: str, plan: dict) -> None:
    safe_relative(rel)
    if rel in FROZEN_NAMES or rel.startswith(FROZEN_PREFIXES):
        raise ContractError(f'protected/frozen path: {rel}')
    if worker not in WORKGROUPS or not path_owned(rel, owners(plan)[worker]):
        raise ContractError(f'{worker} does not own {rel}')


def pinned_contract_digests(packet: Path) -> dict[str, str]:
    return {p.relative_to(packet).as_posix(): digest(p.read_bytes())
            for p in sorted((packet / 'CONTRACTS').rglob('*')) if p.is_file()}


def verify_contract_digests(packet: Path, expected: dict[str, str]) -> None:
    got = pinned_contract_digests(packet)
    if got != expected:
        diff = sorted(set(got) ^ set(expected) | {k for k in got.keys() & expected.keys() if got[k] != expected[k]})
        raise ContractError(f'contract hashes mismatch: {diff}')
