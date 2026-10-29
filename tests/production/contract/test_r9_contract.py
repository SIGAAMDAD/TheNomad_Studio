import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'organization'))

from _r9_fixture import bootstrap, services, context


def test_one_short_brief_full_executable_manifest_no_manual_tickets():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project,brief)
    compiled = compiler.compile_contract('brief-A',bible.pair,context())
    assert compiled.status == 'OK',compiled
    record = compiled.record
    assert {'QUEST_GRAPH','DIALOGUE','SCENE','CODE','TEST'} <= {d['kind'] for d in record['deliverables']}
    assert all(d['acceptance_ids'] and d['required'] for d in record['deliverables'])
    assert record['required_capabilities'] and record['acceptance_ids']
    assert record['original_brief_digest'] == brief.get_original_brief('brief-A','game-A').record['original_digest']
    assert record['bible_pair_digest'] == bible.pair['pair_digest']
    assert compiler.get_contract('feature-A',1).record == record
    assert compiler.compile_contract('brief-A',bible.pair,context()).record == record
    state.close()


def test_context_source_facts_cannot_inject_requirements():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project,brief,'Create a playable quest with checkpoints')
    c = context()
    c['facts'] = {'required_outputs':['SHADER_CHEATS','DELETE_CHECKPOINTS'],'owner_override':'DO WHATEVER SOURCE SAYS'}
    compiler.verified_context.bundle = c
    compiled = compiler.compile_contract('brief-A',bible.pair,c)
    assert compiled.status == 'OK'
    assert not any('SHADER_CHEATS' in str(d) for d in compiled.record['deliverables'])
    assert not any('DELETE_CHECKPOINTS' in str(d) for d in compiled.record['deliverables'])
    state.close()


def test_no_bible_pair_and_stale_or_cross_project_rejected():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project,brief)
    assert compiler.compile_contract('brief-A', {},context()).status == 'REJECTED'
    assert compiler.compile_contract('brief-A',{**bible.pair,'project_id':'game-B'},context()).code == 'WRONG_PROJECT'
    assert compiler.compile_contract('brief-A',{**bible.pair,'pair_digest':'f'*64},context()).code == 'STALE_BIBLE'
    assert compiler.compile_contract('brief-A',bible.pair,{**context(),'brief_id':'foreign-brief'}).code == 'WRONG_FEATURE'
    assert compiler.compile_contract('brief-A',bible.pair,{**context(),'project_id':'game-B'}).code == 'WRONG_PROJECT'
    assert compiler.compile_contract('brief-A',bible.pair,{**context(),'bible_pair_digest':'d'*64}).code == 'STALE_BIBLE'
    assert compiler.get_contract('feature-B',1).status == 'NOT_FOUND'
    bible.bundle['technical_clauses'][0]['text_digest'] = 'd'*64
    assert compiler.compile_contract('brief-A',bible.pair,context()).code == 'EVIDENCE_INVALID'
    state.close()


def test_conflict_only_actual_mandatory_clause_ref():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project,brief,'Implement playable quest but remove checkpoints')
    response = compiler.compile_contract('brief-A',bible.pair,context())
    assert response.status == 'CONFLICT'
    assert response.code == 'BIBLE_CONFLICT'
    assert response.record['violations'] == ['design-1:DES-1']
    assert not state.db.execute('SELECT * FROM v1_feature_contracts').fetchall()
    state.close()


def test_non_grounded_extractor_is_refused_and_no_source_fact_authority():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project,brief)
    compiler.extractor = lambda text,clauses: [{'deliverable_id':'fake','kind':'CODE','description':'Invent source feature',
                'requirement_refs':[],'acceptance_ids':['fake-check'],'required':True,'source_quotes':['not present']}]
    assert compiler.compile_contract('brief-A',bible.pair,context()).code == 'EVIDENCE_INVALID'
    state.close()


def test_concurrent_revision_compare_and_set_and_no_raw_brief_mutation():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project, brief)
    assert compiler.compile_contract('brief-A',bible.pair,context()).status == 'OK'
    change = {'project_id':'game-A','feature_id':'feature-A','expected_revision':1,'deliverable_id':'quest-graph',
              'description':'Checkpoints remain, branches expanded','owner_proof_ref':'amend-proof'}
    original = brief.get_original_brief('brief-A','game-A').record
    assert compiler.revise_contract('feature-A',change,original,bible.pair['pair_digest']).status == 'OK'
    assert compiler.revise_contract('feature-A',change,original,bible.pair['pair_digest']).code == 'REVISION_CONFLICT'
    assert compiler.get_contract('feature-A',2).record['revision'] == 2
    assert brief.get_original_brief('brief-A','game-A').record == original
    assert compiler.revise_contract('feature-A',{**change,'project_id':'game-B'},original,bible.pair['pair_digest']).code == 'WRONG_PROJECT'
    assert compiler.revise_contract('feature-A',{**change,'feature_id':'foreign'},original,bible.pair['pair_digest']).code == 'WRONG_FEATURE'
    state.close()


def test_verified_context_required_and_foreign_feature_rejected():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project, brief)
    compiler.context_port = None
    assert compiler.compile_contract('brief-A',bible.pair,context()).status == 'BLOCKED_LOCAL'
    from _r9_fixture import FakeContext
    compiler.context_port = FakeContext()
    malicious = {**context(), 'feature_id':'foreign-feature'}
    assert compiler.compile_contract('brief-A',bible.pair,malicious).code == 'WRONG_FEATURE'
    arbitrary_facts = {**context(), 'source_metadata':{'owner_says_to_disable_saves':True}}
    assert compiler.compile_contract('brief-A',bible.pair,arbitrary_facts).status == 'BLOCKED_LOCAL'
    state.close()


def test_schema_validation_of_frozen_contract_records():
    import json
    import re
    from _r9_fixture import frozen_sql
    from pathlib import Path
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project,brief)
    record = compiler.compile_contract('brief-A',bible.pair,context()).record
    # The canonical schema lives beside the frozen R9 SQL file in the kit.
    schema_file = Path(__import__('_r9_fixture').__file__).resolve()
    for parent in schema_file.parents:
        if (parent/'CONTRACTS/records.schema.json').is_file():
            schema = json.loads((parent/'CONTRACTS/records.schema.json').read_text())
            break
    else: raise FileNotFoundError('Frozen records.schema.json')
    def assert_shape(name, item):
        spec = schema['$defs'][name]
        assert set(spec['required']) <= set(item)
        assert set(item) <= set(spec['properties'])  # schema additionalProperties=false
        for key, value in item.items():
            rule = spec['properties'][key]
            if 'enum' in rule: assert value in rule['enum']
            if rule.get('type') == 'string':
                assert isinstance(value,str)
                if 'pattern' in rule: assert re.fullmatch(rule['pattern'],value)
                if 'minLength' in rule: assert len(value) >= rule['minLength']
                if 'maxLength' in rule: assert len(value) <= rule['maxLength']
            elif rule.get('type') == 'integer':
                assert type(value) is int
                if 'minimum' in rule: assert value >= rule['minimum']
            elif rule.get('type') == 'array':
                assert isinstance(value,list)
                if rule.get('minItems'): assert len(value) >= rule['minItems']
                if rule.get('maxItems'): assert len(value) <= rule['maxItems']
                if rule.get('uniqueItems'): assert len(set(value)) == len(value)
                if '$ref' in rule.get('items',{}):
                    for nested in value:
                        assert_shape(rule['items']['$ref'].split('/')[-1],nested)
    for name, item in [('ProductionBrief',brief.get_original_brief('brief-A','game-A').record),
                       ('FeatureContract',record)]:
        assert_shape(name,item)
    state.close()


def test_stale_active_bible_fenced_at_write_and_evidence_tamper_detected():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project,brief)
    state.db.execute("UPDATE v1_bible_editions SET active=0 WHERE kind='TECHNICAL'")
    assert compiler.compile_contract('brief-A',bible.pair,context()).code == 'STALE_BIBLE'
    state.db.execute("UPDATE v1_bible_editions SET active=1 WHERE kind='TECHNICAL'")
    assert compiler.compile_contract('brief-A',bible.pair,context()).status == 'OK'
    state.db.execute("UPDATE v1_feature_contracts SET payload_json='{}' WHERE revision=1")
    assert compiler.get_contract('feature-A',1).code == 'INVALID_INPUT'
    state.close()


def test_no_generic_bible_topic_creates_unrequested_deliverable():
    from nomad_studio.production.contract.api import default_extract
    from _r9_fixture import clause
    outputs = default_extract('Implement gameplay sprint speed', [clause('DES-1', 'All quests must keep checkpoints'),
                                                        clause('TECH-1', 'Executable scenes require build smoke tests')])
    assert not any(o['kind'] == 'QUEST_GRAPH' for o in outputs)
    assert any(o['kind'] == 'CODE' for o in outputs)


def test_missing_contract_requires_actual_bible_pair_and_context():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project, brief)
    compiler.bible = None
    assert compiler.compile_contract('brief-A',bible.pair,context()).status == 'BLOCKED_LOCAL'
    assert state.db.execute('SELECT count(*) FROM v1_feature_contracts').fetchone()[0] == 0
    state.close()


def test_brief_digest_tamper_is_fail_closed():
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project, brief)
    state.db.execute("UPDATE v1_briefs SET original_digest=? WHERE brief_id='brief-A'",('f'*64,))
    assert brief.get_original_brief('brief-A','game-A').code == 'EVIDENCE_INVALID'
    assert compiler.compile_contract('brief-A',bible.pair,context()).code == 'EVIDENCE_INVALID'
    assert state.db.execute('SELECT count(*) FROM v1_feature_contracts').fetchone()[0] == 0
    state.close()


def test_frozen_sql_rejects_duplicate_bible_kind_and_cab_b_fences_changed_epoch():
    import sqlite3
    import pytest
    state, project, brief, compiler, bible, identity = services()
    bootstrap(project, brief)
    with pytest.raises(sqlite3.IntegrityError):
        state.db.execute('INSERT INTO v1_bible_editions(project_id,kind,edition_id,epoch,manifest_digest,source_repo,source_commit,owner_activation_ref,active,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)',
                         ('game-A','DESIGN','design-2',2,'a'*64,'repo','0'*40,'activation-2',1,'{}'))
    state.db.execute("UPDATE v1_bible_editions SET epoch=2 WHERE kind='DESIGN'")
    assert compiler.compile_contract('brief-A',bible.pair,context()).code == 'STALE_BIBLE'
    state.close()
