import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'organization'))

from _r9_fixture import OWNER, bootstrap, services, context
from nomad_studio.production.intake._support import Result, text_digest


def test_admit_once_original_bytes_preserved_and_idempotent():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project, brief, 'Implement playable quest with dialogue.  \nKeep checkpoints.')
    record = brief.get_original_brief('brief-A','game-A')
    assert record.status == 'OK'
    assert record.record['original_text'].endswith('  \nKeep checkpoints.')
    assert record.record['original_digest'] == text_digest(record.record['original_text'])
    repeated = brief.admit_brief({'project_id':'game-A','brief_id':'brief-A','feature_id':'feature-A',
                                  'owner_command_ref':'command-session','original_text':record.record['original_text']},
                                 Result('OK','OK',{'principal_id':'owner-A'},'command-session'))
    assert repeated.status == 'OK'
    assert brief.get_original_brief('brief-A','game-B').code == 'WRONG_PROJECT'
    state.close()


def test_brief_rejects_command_replay_text_swap_and_cross_project():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project, brief)
    owner = Result('OK','OK',{'principal_id':'owner-A'},'command-session')
    changed = {'project_id':'game-A','brief_id':'brief-A','feature_id':'feature-A',
               'owner_command_ref':'command-session','original_text':'Changed command'}
    assert brief.admit_brief(changed,owner).code == 'IDEMPOTENCY_CONFLICT'
    assert brief.admit_brief({**changed, 'brief_id':'brief-B','feature_id':'feature-B'},owner).code == 'IDEMPOTENCY_CONFLICT'
    assert brief.admit_brief({**changed,'project_id':'game-B'},owner).status == 'NOT_FOUND'
    assert brief.admit_brief(changed,OWNER).code == 'EVIDENCE_INVALID'
    state.close()


def test_amend_requires_prior_contract_and_actual_owner_decision():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project,brief)
    assert compiler.compile_contract('brief-A',bible.pair,context()).status == 'OK'
    change = {'project_id':'game-A','expected_revision':1,'deliverable_id':'quest-graph',
              'description':'Add two checkpoint branches','owner_proof_ref':'amend-proof'}
    assert brief.amend_brief('feature-A',change, Result('OK','OK',{'principal_id':'owner-B'},'owner-session')).code == 'PERMISSION_DENIED'
    assert brief.amend_brief('feature-A',{**change,'owner_proof_ref':'forged'},OWNER).code == 'OWNER_ACTION_REQUIRED'
    assert brief.amend_brief('feature-A',change,OWNER).status == 'OK'
    assert compiler.get_contract('feature-A',1).record['deliverables'][0]['description'] != 'Add two checkpoint branches'
    assert compiler.get_contract('feature-A',2).status == 'OK'
    assert brief.amend_brief('feature-A',change,OWNER).code == 'REVISION_CONFLICT'
    state.close()


def test_brief_rejected_if_project_is_paused_or_identity_unverified():
    state, project, brief, compiler, bible, identity = services()
    from _r9_fixture import OWNER
    spec = {'project_id':'game-A', 'owner_principal_id':'owner-A', 'constitution_digest':'d'*64,
            'initial_state':'PAUSED', 'repository_ref':'repo'}
    assert project.register_project(spec, OWNER).status == 'OK'
    command = {'project_id':'game-A', 'feature_id':'feature-A', 'brief_id':'brief-A',
               'original_text':'Implement playable quest', 'owner_command_ref':'command-session'}
    result = brief.admit_brief(command, Result('OK','OK',{'principal_id':'owner-A'},'command-session'))
    assert result.code == 'SCOPE_EXCEEDED'
    project.set_project_state('game-A',1,'ACTIVE',OWNER)
    assert brief.admit_brief(command, Result('REJECTED','UNAUTHENTICATED')).code == 'EVIDENCE_INVALID'
    assert brief.admit_brief(command, Result('OK','OK',{'principal_id':'owner-A'},'command-session')).status == 'OK'
    state.close()
