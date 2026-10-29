import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _r9_fixture import FakeCabBStore, OWNER, services
from nomad_studio.production.intake._support import Result


def test_bootstrap_idempotent_without_department_or_github():
    state, project, _, _, _, _ = services()
    spec = {'project_id': 'game-A', 'owner_principal_id': 'owner-A', 'constitution_digest':'d'*64,
            'initial_state': 'INCUBATING', 'repository_ref':'verified/game-source'}
    assert project.register_project(spec, OWNER).status == 'OK'
    assert project.register_project(spec, OWNER).status == 'OK'
    got = project.get_project('game-A')
    assert got.record['authority_epoch'] == 1 and got.record['state'] == 'INCUBATING'
    assert project.set_project_state('game-A', 1, 'ACTIVE', OWNER).record['authority_epoch'] == 2
    assert project.set_project_state('game-A', 1, 'PAUSED', OWNER).code == 'REVISION_CONFLICT'
    assert project.get_project('game-A').record['state'] == 'ACTIVE'
    assert len(project.list_owner_projects('owner-A')) == 1
    state.close()


def test_bootstrap_rejects_unauthenticated_wrong_owner_and_constitution_swap():
    state, project, _, _, _, _ = services()
    spec = {'project_id': 'game-A', 'owner_principal_id': 'owner-A', 'constitution_digest':'d'*64,
            'initial_state': 'ACTIVE'}
    assert project.register_project(spec, Result('OK','OK', {'principal_id':'owner-B'}, 'owner-session')).code == 'PERMISSION_DENIED'
    assert project.register_project(spec, Result('OK','OK', {'principal_id':'owner-A'})).code == 'EVIDENCE_INVALID'
    assert project.register_project(spec, OWNER).status == 'OK'
    assert project.register_project({**spec,'constitution_digest':'f'*64}, OWNER).status == 'CONFLICT'
    assert project.set_project_state('game-A', 1, 'ACTIVE', Result('REJECTED','UNAUTHENTICATED')).code == 'UNAUTHENTICATED'
    assert project.get_project('unknown').status == 'NOT_FOUND'
    state.close()


def test_real_frozen_schema_composite_fks():
    state = FakeCabBStore()
    tables = {r['name'] for r in state.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'v1_projects','v1_project_constitutions','v1_briefs','v1_feature_contracts'} <= tables
    foreign_keys = state.db.execute('PRAGMA foreign_key_list(v1_feature_contracts)').fetchall()
    assert any(k['table'] == 'v1_briefs' for k in foreign_keys)
    state.close()


def test_paused_project_state_epoch_is_serialized_in_sqlite():
    state, project, _, _, _, _ = services()
    spec = {'project_id':'game-A','owner_principal_id':'owner-A','constitution_digest':'d'*64,
            'initial_state':'ACTIVE','repository_ref':'repo'}
    assert project.register_project(spec,OWNER).status == 'OK'
    assert project.set_project_state('game-A',1,'PAUSED',OWNER).status == 'OK'
    assert project.get_project('game-A').record['state'] == 'PAUSED'
    assert project.get_project('game-A').record['authority_epoch'] == 2
    assert project.set_project_state('game-A',1,'ACTIVE',OWNER).status == 'CONFLICT'
    state.close()
