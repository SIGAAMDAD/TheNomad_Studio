"""Deterministic, adversarial contract tests against the real R9 graph validator.

All generated work is synthetic; no agent, repository or native provider is invoked.
"""
from __future__ import annotations

from copy import deepcopy
import random

import pytest

from nomad_studio.production.workgraph.core import (
    Refused, critical_path, hash_record, make_graph, resource_key, validate_graph,
)

D = 'a' * 64
E = 'b' * 64
F = 'c' * 64


def make_contract(size: int) -> dict:
    return {
        'project_id': 'project', 'feature_id': 'feature', 'brief_id': 'brief',
        'original_brief_digest': D, 'bible_pair_digest': E, 'revision': 1,
        'contract_digest': F, 'scope_digest': D, 'budget_limit_units': size * 3,
        'acceptance_ids': ['accept'], 'required_capabilities': ['code'],
        'unresolved_boundary_refs': [],
        'deliverables': [
            {'deliverable_id': f'out{i}', 'kind': 'CODE',
             'description': 'deterministic artifact', 'requirement_refs': [],
             'acceptance_ids': ['accept'], 'required': True}
            for i in range(size)
        ],
    }


def make_plan(size: int, seed: int) -> dict:
    rng = random.Random(seed)
    tasks = []
    for i in range(size):
        possible = list(range(i))
        deps = sorted(rng.sample(possible, min(len(possible), rng.randrange(0, 4))))
        tasks.append({
            'task_id': f't{i}', 'cabal_id': 'cabal',
            'dependencies': [f't{j}' for j in deps],
            'required_skills': ['code'], 'owned_resources': [f'src/task{i}'],
            'input_digests': [D], 'outputs': [f'out{i}'],
            'acceptance_ids': ['accept'], 'budget_limit_units': 1,
        })
    return {
        'project_id': 'project', 'feature_id': 'feature',
        'bible_pair_digest': E, 'feature_contract_digest': F, 'tasks': tasks,
    }


@pytest.mark.parametrize('seed', range(120))
def test_random_dags_roundtrip_immutable_and_complete(seed):
    size = 1 + seed % 26
    contract = make_contract(size)
    plan = make_plan(size, seed)
    saved = deepcopy(plan)
    graph = make_graph(contract, plan, 1)
    assert plan == saved, 'planning must not mutate caller-owned input'
    assert graph['graph_digest'] == hash_record({k:v for k,v in graph.items() if k != 'graph_digest'})
    validate_graph(graph, F, deliverables={f'out{i}' for i in range(size)},
                   acceptance={'accept'}, budget=size * 3)
    assert {t['task_id'] for t in graph['tasks']} == {f'feature:r1:t{i}' for i in range(size)}
    trace = critical_path(graph, set())
    by_id = {t['task_id']: t for t in trace}
    assert len(by_id) == size
    for task in graph['tasks']:
        for dep in task['dependencies']:
            assert by_id[dep]['remaining_critical_path'] > by_id[task['task_id']]['remaining_critical_path']
    # Completed nodes must never count as an unmet dependency.
    chosen = {graph['tasks'][0]['task_id']}
    for item in critical_path(graph, chosen):
        assert not (chosen & set(item['unmet_dependencies']))


@pytest.mark.parametrize('bad', ['../file', 'a/../b', 'a//b', '/absolute', 'a/',
                                 'A/.git/config', 'a\\b', 'a/./b', 'a/Trailing.'])
def test_path_aliases_rejected(bad):
    with pytest.raises(Refused):
        resource_key(bad)


@pytest.mark.parametrize('mutator,expected', [
    (lambda p:p['tasks'][0]['dependencies'].append('missing'), 'WRONG_FEATURE'),
    (lambda p:p['tasks'][0]['required_skills'].append('unapproved'), 'INVALID_INPUT'),
    (lambda p:p['tasks'][0]['outputs'].append('out1'), 'INVALID_INPUT'),
    (lambda p:p['tasks'][0].update(budget_limit_units=99), 'CAPACITY_LIMIT'),
    (lambda p:p.update(project_id='other'), 'WRONG_PROJECT'),
    (lambda p:p.update(bible_pair_digest=D), 'STALE_BIBLE'),
    (lambda p:(p['tasks'][1]['dependencies'].append('t2'), p['tasks'][2]['dependencies'].append('t1')), 'INVALID_INPUT'),
    (lambda p:p['tasks'][1].update(owned_resources=['src/task0/file']), 'LEASE_CONFLICT'),
])
def test_invalid_plans_fail_closed(mutator, expected):
    contract = make_contract(3)
    plan = make_plan(3, 0)
    # Make an independent graph for deterministic cycle and resource collision probes.
    for task in plan['tasks']:
        task['dependencies'] = []
    mutator(plan)
    with pytest.raises(Refused) as exc:
        make_graph(contract, plan, 1)
    assert exc.value.code == expected


def test_resource_overlap_is_allowed_when_explicitly_dependency_ordered():
    plan = make_plan(2, 3)
    plan['tasks'][0]['owned_resources'] = ['content/quest']
    plan['tasks'][1]['owned_resources'] = ['content/quest/script']
    plan['tasks'][1]['dependencies'] = ['t0']
    result = make_graph(make_contract(2), plan, 1)
    assert len(result['tasks']) == 2


def test_digest_detects_mutated_graph_even_if_public_hash_is_unchanged():
    graph = make_graph(make_contract(2), make_plan(2, 1), 1)
    graph['tasks'][0]['owned_resources'] = ['src/other']
    with pytest.raises(Refused):
        validate_graph(graph, F)
