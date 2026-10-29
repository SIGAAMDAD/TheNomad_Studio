"""Deterministic R9 pure-unit adversarial coverage of production boundaries.

These tests exercise the real validators, contracts and SQLite schema; they do not
claim to run external providers, ChatGPT sessions, or game engine builds.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from hashlib import sha256
import copy
import inspect
import json
from pathlib import Path
import random
import sqlite3

import pytest

from nomad_studio.contracts import runtime_ports_v1 as ports
from nomad_studio.production.director.commands import CommandService, OPERATIONS
from nomad_studio.production.director.api import _TRANSITIONS
from nomad_studio.production.workgraph.core import (
    Refused, critical_path, hash_record, make_graph, validate_graph, resource_key,
)

ROOT = Path(__file__).resolve().parents[2]
D = 'a' * 64
E = 'b' * 64
F = 'c' * 64
PROOF = 'proof/native-owner'


def command(**overrides):
    d = dict(command_id='cmd1', operation='production.submit_brief',
             owner_principal_id='owner/one', request_digest=D,
             auth_proof_ref=PROOF, idempotency_key='key1', issued_at_us=42,
             project_id='project-one')
    d.update(overrides)
    return d


def owner(**overrides):
    record = dict(principal_id='owner/one', principal_kind='OWNER',
                  action_digest=D, simulated=False)
    receipt = overrides.pop('receipt_ref', PROOF)
    record.update(overrides)
    return ports.Result('OK', 'OK', record, receipt_ref=receipt)


def make_contract(n):
    return dict(project_id='p', feature_id='f', brief_id='b', original_brief_digest=D,
                bible_pair_digest=E, revision=1, contract_digest=F, scope_digest=D,
                budget_limit_units=n*3, acceptance_ids=['a'], required_capabilities=['code'],
                unresolved_boundary_refs=[], deliverables=[dict(deliverable_id=f'o{i}',kind='CODE',
                description='source artifact',requirement_refs=[],acceptance_ids=['a'],required=True)
                for i in range(n)])


def make_plan(n, seed=0):
    rng=random.Random(seed)
    return dict(project_id='p',feature_id='f',bible_pair_digest=E,
                feature_contract_digest=F,tasks=[dict(task_id=f't{i}',cabal_id='c',
                dependencies=([f't{rng.randrange(i)}'] if i and rng.getrandbits(1) else []),
                required_skills=['code'],owned_resources=[f'src/component{i}'],
                input_digests=[D],outputs=[f'o{i}'],acceptance_ids=['a'],
                budget_limit_units=1) for i in range(n)])


@pytest.mark.parametrize('field', ['command_id','operation','owner_principal_id',
    'request_digest','auth_proof_ref','idempotency_key','issued_at_us','project_id'])
@pytest.mark.parametrize('bad', [None, '', '\x00', '../escape', [], {}, True])
def test_command_injection_and_missing_required_fields(field, bad):
    data=command()
    data[field]=bad
    assert not CommandService._valid(data)
    del data[field]
    assert not CommandService._valid(data)


@pytest.mark.parametrize('operation', sorted(OPERATIONS))
def test_all_owner_operations_reject_identity_mismatch(operation):
    data=command(operation=operation)
    if operation in {'production.pause','production.resume','production.cancel',
                     'production.status','production.inspect','production.request_changes'}:
        data['feature_id']='f'
    assert CommandService._valid(data)
    for mutation in [dict(principal_id='other'), dict(principal_kind='WORKER'),
                     dict(action_digest=F), dict(simulated=True), dict(simulated='false'),
                     dict(receipt_ref='other')]:
        assert not CommandService._authenticated(data, owner(**mutation))


@pytest.mark.parametrize('seed', range(160))
def test_randomized_dag_behavior_and_immutability(seed):
    size=1+(seed%25)
    plan=make_plan(size, seed)
    before=copy.deepcopy(plan)
    graph=make_graph(make_contract(size),plan,1)
    assert plan==before
    assert graph['graph_digest']==hash_record({k:v for k,v in graph.items() if k!='graph_digest'})
    assert validate_graph(graph,F,deliverables={f'o{i}' for i in range(size)},
                          acceptance={'a'},budget=3*size)
    trace=critical_path(graph,set())
    assert len(trace)==size
    for item in trace:
        assert item['remaining_critical_path']>=1


@pytest.mark.parametrize('seed', range(80))
def test_graph_rejects_mutation_of_referenced_task(seed):
    graph=make_graph(make_contract(3),make_plan(3,seed),1)
    entry=graph['tasks'][seed%3]
    entry['input_digests']=[F]
    with pytest.raises(Refused):
        validate_graph(graph,F)


@pytest.mark.parametrize('seed', range(50))
def test_graph_fences_foreign_feature_and_bible(seed):
    size=2+(seed%7)
    p=make_plan(size,seed)
    p['feature_id']='foreign' if seed%2 else 'f'
    p['bible_pair_digest']=F if seed%2==0 else E
    with pytest.raises(Refused) as err:
        make_graph(make_contract(size),p,1)
    assert err.value.code in ('WRONG_FEATURE','STALE_BIBLE')


@pytest.mark.parametrize('bad', ['src/../escape','src/./alias','src//dupe','C:/system',
     '/root/path','src\\alias','src/last.','src/space ','src/.git/config','src//'])
def test_resource_aliases_rejected(bad):
    with pytest.raises(Refused):resource_key(bad)


@pytest.mark.parametrize('seed', range(80))
def test_graph_disjoint_and_parent_child_lease_semantics(seed):
    p=make_plan(2,seed)
    p['tasks'][0]['dependencies']=[]
    p['tasks'][1]['dependencies']=[]
    p['tasks'][0]['owned_resources']=['Content/Quest']
    p['tasks'][1]['owned_resources']=['content/quest/script']
    with pytest.raises(Refused) as err:make_graph(make_contract(2),p,1)
    assert err.value.code=='LEASE_CONFLICT'
    p['tasks'][1]['dependencies']=['t0']
    assert len(make_graph(make_contract(2),p,1)['tasks'])==2


@pytest.mark.parametrize('seed',range(40))
def test_stale_or_undeclared_lifecycle_jump_is_not_available(seed):
    rng=random.Random(seed)
    states=list(_TRANSITIONS)
    for _ in range(25):
        src,dst=rng.sample(states,2)
        if dst not in _TRANSITIONS[src]:
            assert dst not in _TRANSITIONS[src]
        else:
            assert src!=dst
    assert not _TRANSITIONS['MERGED'] and not _TRANSITIONS['CANCELLED']


def test_protocol_collection_is_frozen_and_required():
    required={'IdentityPort','BiblePort','AuthorityPort','ProjectPort','BriefPort',
      'FeatureContractPort','DirectorPort','WorkGraphPort','SchedulingPort',
      'CabalPort','CouncilPort','CapabilityPort','WorkerHostPort','AgentExecutionPort',
      'ContextPort','ProjectAdapterPort','ArtifactPort','RepositoryPort','QualityPort',
      'CommandPort','OversightNoticePort'}
    found={n for n,v in vars(ports).items() if n.endswith('Port') and inspect.isclass(v)}
    assert required <= found
    for name in required:
        methods={n for n,v in vars(getattr(ports,name)).items() if not n.startswith('_') and callable(v)}
        assert methods, name
        for method in methods:
            assert inspect.signature(getattr(getattr(ports,name),method))


def test_frozen_proof_and_result_objects_reject_mutation():
    for obj in (ports.Result('OK','OK',{}),ports.Decision(True,'ALLOW','OK','subject'),
                ports.SourceFence('project','repo','branch','a'*40,D,'feature',E)):
        for f in fields(obj):
            with pytest.raises((FrozenInstanceError,AttributeError)):
                setattr(obj,f.name,'tampered')


def test_56_table_schema_and_owner_registry_remain_identical():
    schema=(ROOT/'CONTRACTS/initial_schema_v1.sql').read_text()
    mapping=json.loads((ROOT/'CONTRACTS/schema_table_owners.json').read_text())['owner_per_table']
    db=sqlite3.connect(':memory:')
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(schema)
        actual={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'v1_%'")}
        assert len(actual)==56 and actual==set(mapping)
        assert all(owner in {'CAB-'+ch for ch in 'ABCDEFGHIJ'}|{'P2'} for owner in mapping.values())
        assert not list(db.execute('PRAGMA foreign_key_check'))
        assert db.execute('PRAGMA foreign_keys').fetchone()[0]==1
    finally:db.close()


@pytest.mark.parametrize('seed',range(60))
def test_fresh_schema_transaction_atomicity_under_simulated_errors(seed):
    schema=(ROOT/'CONTRACTS/initial_schema_v1.sql').read_text()
    db=sqlite3.connect(':memory:',isolation_level=None)
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(schema)
    try:
        project=f'project-{seed}'
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO v1_projects VALUES(?,?,?,?,?)',(project,'owner',D,'ACTIVE',1))
        if seed%2:
            db.rollback()
            assert db.execute('SELECT 1 FROM v1_projects WHERE project_id=?',(project,)).fetchone() is None
        else:
            db.commit()
            assert db.execute('SELECT 1 FROM v1_projects WHERE project_id=?',(project,)).fetchone()
        assert not list(db.execute('PRAGMA foreign_key_check'))
    finally:db.close()


def test_schema_and_records_are_byte_addressed():
    for name in ('initial_schema_v1.sql','schema_table_owners.json','records.schema.json'):
        data=(ROOT/'CONTRACTS'/name).read_bytes()
        assert len(data)>100
        assert sha256(data).hexdigest()!=sha256(data+b'!').hexdigest()
    rec=json.loads((ROOT/'CONTRACTS/records.schema.json').read_text())
    assert len(rec.get('$defs',{}))>=10
    assert all(obj.get('additionalProperties') is False for obj in rec['$defs'].values() if obj.get('type')=='object')
