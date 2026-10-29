from __future__ import annotations

from hashlib import sha256
import json
import pathlib

import pytest

from nomad_studio.registry.api import CapabilityService
from nomad_studio.registry.catalog import (
    BASE_FILE_SHA256, FMOD_FILE_SHA256, Specialization, load_source_roster, project_catalog,
)
from nomad_studio.registry.effort import select_effort
from nomad_studio.registry.skills import verify_installed_skill, verify_adapter_manifest, SkillVerificationError
from tests.registry.support import SQLiteTestDriver, SHA, CONTRACT


@pytest.fixture
def rig():
    db=SQLiteTestDriver();db.seed()
    project=load_source_roster('proj')
    first=project.specializations['rpg-ai-programmer']
    second=project.specializations['rpg-build-engineer']
    content={'rpg-ai-programmer':b'# AI engineer pinned content',
             'rpg-build-engineer':b'# Build engineer pinned content'}
    pins={s.id:{'uri':s.skill_uri,'version':s.skill_version,'sha256':sha256(content[s.id]).hexdigest()}
          for s in (first,second)}
    observed={'observed':True,'evidence_ref':'observer:real', 'workers':[
        {'worker_id':'worker1','project_id':'proj','cabal_id':'cabal','observed':True,'evidence_ref':'ob:worker1',
         'verified_skills':['rpg-ai-programmer'],'available':True,'active_wip':0,'wip_limit':2,
         'session_quota':2,'active_sessions':0,'offered_efforts':['high','extra_high'],'success_rate':1.0},
        {'worker_id':'worker2','project_id':'proj','cabal_id':'cabal','observed':True,'evidence_ref':'ob:worker2',
         'verified_skills':['rpg-build-engineer'],'available':True,'active_wip':0,'wip_limit':1,
         'session_quota':1,'active_sessions':0,'offered_efforts':['high'],'success_rate':1.0},
    ]}
    def installed(uri):
        for s in (first,second):
            if s.skill_uri==uri:
                return {'uri':uri,'version':s.skill_version,'content':content[s.id],
                        'evidence_ref':'installed:'+s.id,'observed':True}
        raise KeyError(uri)
    manifest={'adapter_id':'godot','source_digest':'f'*64,
              'capabilities':['scene','terrain','assets','audio'],'evidence_ref':'adapter:godot','observed':True}
    deps={'reader':db,'writer':db,'installed_skill_reader':installed,
          'active_bible_reader':lambda project: SHA,
          'worker_observer':lambda project: observed,
          'adapter_reader':lambda project,adapter:manifest}
    service=CapabilityService(deps)
    assert service.import_project_roster('proj',project).status=='OK'
    return db,service,pins,observed,manifest


def task(idx, skills, **overrides):
    d={'project_id':'proj','feature_id':'feature','task_id':f'task-{idx}',
       'graph_revision':1,'cabal_id':'cabal','bible_pair_digest':SHA,
       'feature_contract_digest':CONTRACT,'dependencies':[],
       'required_skills':skills,'owned_resources':['src'],
       'input_digests':[],'outputs':['build'],'acceptance_ids':[],
       'budget_limit_units':1,'task_digest':'1'*64}
    return {**d,**overrides}


def graph(tasks):
    return {'graph_id':'graph','project_id':'proj','feature_id':'feature','revision':1,
        'bible_pair_digest':SHA,'feature_contract_digest':CONTRACT,'graph_digest':'3'*64,'tasks':tasks}


def feature():
    return {'project_id':'proj','feature_id':'feature','brief_id':'brief',
        'original_brief_digest':'1'*64,'bible_pair_digest':SHA,'revision':1,
        'contract_digest':CONTRACT,'scope_digest':'e'*64,
        'deliverables':[{'deliverable_id':'d-1','kind':'CODE','description':'source',
            'requirement_refs':[],'acceptance_ids':[],'required':True}], 'acceptance_ids':[],
        'budget_limit_units':10,'required_capabilities':[],
        'unresolved_boundary_refs':[]}


def test_exact_174_source_role_ids_survive_cabal_assignment(rig):
    _,svc,_,_,_=rig
    source=json.loads((pathlib.Path(__file__).parents[2]/'src/nomad_studio/registry/data/base_roster.json').read_text())
    ids={r['id'] for r in source['roles']}
    assert len(ids)==174
    assert ids == set(svc.project_catalogs['proj'].specializations)
    assert all(x.startswith('rpg-') for x in ids)
    a=svc.project_catalogs['proj'].assign_to_cabal(sorted(ids)[:100],'cabal')
    b=svc.project_catalogs['proj'].assign_to_cabal(sorted(ids)[100:],'other_cabal')
    assert set(a['specialization_ids']) | set(b['specialization_ids']) == ids
    assert set(a['specialization_ids']).isdisjoint(b['specialization_ids'])
    assert not hasattr(svc.project_catalogs['proj'],'heads')
    assert not hasattr(next(iter(svc.project_catalogs['proj'].specializations.values())),'department')
    # Custom unrelated project may have one specialist, not 174 mandatory sessions.
    custom=project_catalog('unrelated',[Specialization('cxx','C++','skills://cxx','1','owner')])
    assert len(custom.specializations)==1
    assert len(load_source_roster('proj',include_fmod=True).specializations)==194


def test_immutable_roster_hashes():
    root=pathlib.Path(__file__).parents[2]/'src/nomad_studio/registry/data'
    assert sha256((root/'base_roster.json').read_bytes()).hexdigest()==BASE_FILE_SHA256
    assert sha256((root/'fmod_overlay.json').read_bytes()).hexdigest()==FMOD_FILE_SHA256


def test_install_only_with_real_bytes_and_evidence(rig):
    db,svc,pins,_,_=rig
    assert svc.install_skill('proj','rpg-ai-programmer',pins['rpg-ai-programmer']).status=='OK'
    assert db.read(lambda c:c.execute('SELECT COUNT(*) FROM v1_skill_capabilities').fetchone()[0])==174
    bad={**pins['rpg-build-engineer'],'sha256':'0'*64}
    assert svc.install_skill('proj','rpg-build-engineer',bad).code=='EVIDENCE_INVALID'
    assert svc.install_skill('other','rpg-ai-programmer',pins['rpg-ai-programmer']).code=='INVALID_INPUT'
    deps={'reader':db,'writer':db,'active_bible_reader':lambda x:SHA}
    missing=CapabilityService(deps)
    assert missing.install_skill('proj','rpg-ai-programmer',pins['rpg-ai-programmer']).status=='REJECTED'
    without_reader=CapabilityService({**deps,'project_catalogs':svc.project_catalogs})
    assert without_reader.install_skill('proj','rpg-build-engineer',pins['rpg-build-engineer']).status=='NOT_VERIFIED'


def test_adapters_are_data_and_must_match_trusted_manifest(rig):
    db,svc,_,_,manifest=rig
    claim={k:manifest[k] for k in ('adapter_id','source_digest','capabilities')}
    assert svc.install_project_adapter('proj',claim).status=='OK'
    resolved=svc.resolve_capabilities('proj',['godot:terrain','godot:audio'],SHA)
    assert resolved.status=='OK'
    claim['source_digest']='0'*64
    assert svc.install_project_adapter('proj',claim).code=='EVIDENCE_INVALID'
    assert svc.resolve_capabilities('foreign',['godot:terrain'],SHA).status=='BLOCKED_LOCAL'
    assert db.read(lambda c:c.execute("SELECT COUNT(*) FROM v1_skill_capabilities WHERE project_scope_id='proj'").fetchone()[0])==178


def test_missing_skill_only_holds_its_task_and_wip_throttles(rig):
    db,svc,pins,_,_=rig
    assert svc.install_skill('proj','rpg-ai-programmer',pins['rpg-ai-programmer']).status=='OK'
    assert svc.sync_worker_qualification('worker1','rpg-ai-programmer','proj').status=='OK'
    specs=[task(1,['rpg-ai-programmer']),task(2,['rpg-nonexistent']),
           task(3,['rpg-ai-programmer']),task(4,['rpg-ai-programmer'])]
    result=svc.allocate_capabilities(feature(),graph(specs))
    assert result.status=='OK'
    assert len(result.record['proposals'])==2
    assert result.record['proposals'][0]['effort_target']=='high' or result.record['proposals'][0]['effort_target']=='extra_high'
    assert [x['task_id'] for x in result.record['held_tasks']]==['task-2','task-4']
    assert result.record['held_tasks'][0]['code']=='PROVIDER_NOT_VERIFIED'
    assert result.record['held_tasks'][1]['code']=='CAPACITY_LIMIT'
    assert result.record['lease_committed'] is False


def test_capacity_and_observed_worker_limits(rig):
    db,svc,pins,snapshot,_=rig
    svc.install_skill('proj','rpg-ai-programmer',pins['rpg-ai-programmer'])
    svc.sync_worker_qualification('worker1','rpg-ai-programmer','proj')
    snapshot['workers'][0]['active_wip']=1
    snapshot['workers'][0]['success_rate']=0.4
    assert svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).record['proposals']==[]
    snapshot['workers'][0]['success_rate']=1.0
    db.write(lambda c:c.execute('UPDATE v1_cabal_capacity SET reserved_units=2'))
    assert svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).record['proposals']==[]
    snapshot['workers'][0]['offered_efforts']=['medium']
    db.write(lambda c:c.execute('UPDATE v1_cabal_capacity SET reserved_units=0'))
    assert svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).record['proposals']==[]


def test_effort_policy():
    assert select_effort('qa',['medium','high','extra_high'])=='extra_high'
    assert select_effort('lead',['medium','high'])=='high'
    assert select_effort('debug',['medium']) is None
    assert select_effort('production',['medium']) is None
    assert select_effort('status',['instant','medium'],persistent=False)=='medium'
    assert select_effort('status',['instant'],persistent=True) is None


def test_stale_bible_and_forged_skill_claim_rejected(rig):
    db,svc,pins,snapshot,_=rig
    assert svc.resolve_capabilities('proj',[], 'd'*64).code=='STALE_BIBLE'
    assert svc.resolve_capabilities('proj',['rpg-ai-programmer'],SHA).status=='BLOCKED_LOCAL'
    snapshot['workers'][0]['verified_skills'].append('rpg-nonexistent')
    assert svc.sync_worker_qualification('worker1','rpg-nonexistent','proj').status=='BLOCKED_LOCAL'
    # Textual task spec claiming installed capabilities cannot change verified DB.
    assert svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).record['proposals']==[]
    assert db.read(lambda c:c.execute('SELECT COUNT(*) FROM v1_worker_capabilities').fetchone()[0])==0


def test_verified_worker_observation_required(rig):
    db,svc,pins,snapshot,_=rig
    svc.install_skill('proj','rpg-ai-programmer',pins['rpg-ai-programmer'])
    snapshot['workers'][0]['observed']=False
    assert svc.sync_worker_qualification('worker1','rpg-ai-programmer','proj').status=='NOT_VERIFIED'
    assert svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).status=='NOT_VERIFIED'


def test_schema_foreign_keys_and_only_owned_writes(rig):
    db,_,_,_,_=rig
    assert db.read(lambda c:c.execute('PRAGMA foreign_keys').fetchone()[0])==1
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.write(lambda c:c.execute('INSERT INTO v1_worker_capabilities VALUES(?,?,?,?)',('w','missing','e',1)))
    assert db.read(lambda c:c.execute('SELECT COUNT(*) FROM v1_worker_capabilities').fetchone()[0])==0


def test_roster_persists_174_as_uninstalled_data_across_restart(rig):
    db,svc,pins,_,_=rig
    assert db.read(lambda c:c.execute("SELECT COUNT(*) FROM v1_skill_capabilities WHERE qualification_tier='roster_only'").fetchone()[0])==174
    assert svc.resolve_capabilities('proj',['rpg-ai-programmer'],SHA).status=='BLOCKED_LOCAL'
    assert svc.install_skill('proj','rpg-ai-programmer',pins['rpg-ai-programmer']).status=='OK'
    restarted=CapabilityService({'reader':db,'writer':db,'active_bible_reader':lambda _:SHA})
    # Reimport on fresh process reconstructs catalog without overwriting verified install.
    assert restarted.import_project_roster('proj',load_source_roster('proj')).status=='OK'
    assert restarted.resolve_capabilities('proj',['rpg-ai-programmer'],SHA).status=='OK'
    assert db.read(lambda c:c.execute('SELECT COUNT(*) FROM v1_skill_capabilities').fetchone()[0])==174


def test_installed_requirements_hold_dependency_and_no_neighbor(rig):
    db,svc,pins,snapshot,_=rig
    pin={**pins['rpg-ai-programmer'],'requirements':['godot:terrain']}
    assert svc.install_skill('proj','rpg-ai-programmer',pin).status=='OK'
    data=svc.resolve_capabilities('proj',['rpg-ai-programmer'],SHA)
    assert data.status=='BLOCKED_LOCAL' and data.record['missing']==['godot:terrain']
    assert svc.sync_worker_qualification('worker1','rpg-ai-programmer','proj').status=='OK'
    result=svc.allocate_capabilities(feature(),graph([
        task(1,['rpg-ai-programmer']),task(2,['rpg-nonexistent'])]))
    assert result.record['proposals']==[] and len(result.record['held_tasks'])==2
    manifest={'adapter_id':'godot','source_digest':'f'*64,'capabilities':['scene','terrain','assets','audio']}
    assert svc.install_project_adapter('proj',manifest).status=='OK'
    assert svc.resolve_capabilities('proj',['rpg-ai-programmer'],SHA).status=='OK'
    assert svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).record['proposals']==[]
    # Worker must also have qualification for required transitive adapter capability.
    snapshot['workers'][0]['verified_skills'].append('godot:terrain')
    assert svc.sync_worker_qualification('worker1','godot:terrain','proj').status=='OK'
    assert len(svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).record['proposals'])==1


def test_task_wip_threshold_and_senior_effort(rig):
    db,svc,pins,snapshot,_=rig
    svc.install_skill('proj','rpg-ai-programmer',pins['rpg-ai-programmer'])
    svc.sync_worker_qualification('worker1','rpg-ai-programmer','proj')
    snapshot['task_classes']={'task-1':'qa'}
    assert svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).record['proposals'][0]['effort_target']=='extra_high'
    snapshot['task_wip']={'task-1':1}
    snapshot['task_wip_limits']={'task-1':1}
    result=svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])]))
    assert result.record['held_tasks'][0]['code']=='CAPACITY_LIMIT'
    snapshot['task_wip_limits']={'task-1':2}
    assert len(svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).record['proposals'])==1


def test_port_signature_all_four_frozen_methods(rig):
    import inspect
    _,svc,_,_,_=rig
    assert list(inspect.signature(svc.resolve_capabilities).parameters)==['project_id','required_skills','bible_pair_digest']
    assert list(inspect.signature(svc.allocate_capabilities).parameters)==['feature_contract','graph']
    assert list(inspect.signature(svc.reserve_budget).parameters)==['feature_id','requested_units','feature_budget_digest']
    assert list(inspect.signature(svc.reconcile_spend).parameters)==['reservation_id','provider_charge_receipt']


def test_no_department_authority_in_executable_registry():
    root=pathlib.Path(__file__).parents[2]/'src/nomad_studio/registry'
    for name in ('api.py','catalog.py','skills.py','effort.py'):
        text=(root/name).read_text()
        assert 'head_role_id' not in text
        assert 'reports_to' not in text
    assert not (root/'prompts.py').exists()


def test_strict_graph_envelope_and_identity_uniqueness(rig):
    _,svc,_,_,_=rig
    invalid=task(1,[], permissions='owner')
    assert svc.allocate_capabilities(feature(),graph([invalid])).code=='INVALID_INPUT'
    duplicate=graph([task(1,[]),task(1,[])])
    assert svc.allocate_capabilities(feature(),duplicate).code=='INVALID_INPUT'
    missing={**feature()}; missing.pop('brief_id')
    assert svc.allocate_capabilities(missing,graph([task(1,[])])).code=='INVALID_INPUT'
    mismatched=graph([task(1,[],bible_pair_digest='0'*64)])
    assert svc.allocate_capabilities(feature(),mismatched).code=='INVALID_INPUT'


def test_shared_role_id_across_distinct_projects_is_project_scoped(rig):
    db,svc,pins,_,_=rig
    db.write(lambda c:c.execute('INSERT INTO v1_projects VALUES(?,?,?,?,?)',
         ('other','owner','a'*64,'ACTIVE',1)))
    original=svc.project_catalogs['proj'].specializations['rpg-ai-programmer']
    alternate=project_catalog('other',[original])
    assert svc.import_project_roster('other',alternate).status=='OK'
    assert svc.install_skill('proj',original.id,pins[original.id]).status=='OK'
    assert svc.install_skill('other',original.id,pins[original.id]).status=='OK'
    keys=db.read(lambda c:[x[0] for x in c.execute(
         'SELECT skill_id FROM v1_skill_capabilities WHERE qualification_tier=? AND payload_json LIKE ?',
         ('verified','%rpg-ai-programmer%'))])
    assert len(keys)==2 and len(set(keys))==2 and all(x.startswith('skill:') for x in keys)
    assert svc.resolve_capabilities('other',[original.id],SHA).status=='OK'


def test_adapter_capability_removal_revokes_qualification(rig):
    db,svc,_,snapshot,manifest=rig
    claim={k:manifest[k] for k in ('adapter_id','source_digest','capabilities')}
    assert svc.install_project_adapter('proj',claim).status=='OK'
    snapshot['workers'][0]['verified_skills'].append('godot:terrain')
    assert svc.sync_worker_qualification('worker1','godot:terrain','proj').status=='OK'
    manifest['source_digest']='e'*64
    manifest['capabilities']=['audio']
    claim={k:manifest[k] for k in ('adapter_id','source_digest','capabilities')}
    assert svc.install_project_adapter('proj',claim).status=='OK'
    assert svc.resolve_capabilities('proj',['godot:terrain'],SHA).status=='BLOCKED_LOCAL'
    assert svc.sync_worker_qualification('worker1','godot:terrain','proj').status=='BLOCKED_LOCAL'
    assert db.read(lambda c:c.execute('SELECT COUNT(*) FROM v1_worker_capabilities WHERE available=0').fetchone()[0])==1


def test_worker_unavailable_cannot_allocate(rig):
    db,svc,pins,snapshot,_=rig
    svc.install_skill('proj','rpg-ai-programmer',pins['rpg-ai-programmer'])
    svc.sync_worker_qualification('worker1','rpg-ai-programmer','proj')
    snapshot['workers'][0]['available']=False
    assert svc.allocate_capabilities(feature(),graph([task(1,['rpg-ai-programmer'])])).status=='NOT_VERIFIED'
