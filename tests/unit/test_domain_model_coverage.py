"""Direct boundary checks for full Studio domain contracts and manifest parser."""
from copy import deepcopy
import json

import pytest
from studio.governance.model import GovernanceError
from studio.governance.service import _hex, _text, _optional_edition, parse_manifest
from studio.production.models import TaskSpec, FeatureBrief


def manifest():
    return {'format':'nomad.bible.manifest.v1',
            'clauses':[{'clause_key':'c1','classification':'requirement',
                        'text':'source-grounded', 'scope':{'project':'p'},
                        'status':'verified',
                        'source':{'document_id':'d','revision':'r','start':0,'end':12},
                        'impacted_task_ids':['task2','task1','task1'],
                        'impacted_artifact_ids':['a1']}],
            'facts':[{'fact_id':'f1','subject_key':'terrain', 'predicate':'material',
                      'value':'sand','status':'verified',
                      'evidence':{'source':'document:d:0-12'}}]}


def encoded(doc):return json.dumps(doc,sort_keys=True,separators=(',',':')).encode()


def test_complete_source_anchored_manifest_parses_sorted_deduplicated_impacts():
    result=parse_manifest(encoded(manifest()))
    assert len(result.clauses)==1
    assert len(result.facts)==1
    assert result.clauses[0].impacted_task_ids==('task1','task2')


@pytest.mark.parametrize('section,field,value',[
    ('clauses','clause_key',''),
    ('clauses','classification','unknown'),
    ('clauses','status','promoted'),
    ('clauses','text',''),
    ('clauses','scope',None),
    ('clauses','source',None),
    ('clauses','impacted_task_ids',[123]),
    ('clauses','impacted_artifact_ids',['good',None]),
    ('facts','fact_id',''),
    ('facts','status','observed'),
    ('facts','subject_key',''),
    ('facts','predicate',''),
    ('facts','evidence',{}),
    ('facts','evidence',{'source':''}),
])
def test_bible_manifest_rejects_unverified_fields(section,field,value):
    doc=manifest()
    doc[section][0][field]=value
    with pytest.raises(GovernanceError):parse_manifest(encoded(doc))


@pytest.mark.parametrize('value',[
    {'format':'different','clauses':[],'facts':[]},
    {'format':'nomad.bible.manifest.v1','clauses':{},'facts':[]},
    {'format':'nomad.bible.manifest.v1','clauses':[],'facts':{}},
    [],42,
])
def test_invalid_manifest_root_or_arrays(value):
    with pytest.raises(GovernanceError):parse_manifest(encoded(value))


@pytest.mark.parametrize('span',[
    {'document_id':'d','revision':'r','start':-1,'end':5},
    {'document_id':'d','revision':'r','start':5,'end':5},
    {'document_id':'d','revision':'r','start':5,'end':4},
    {'document_id':'d','revision':'r','start':'0','end':4},
    {'document_id':'d','revision':'r','start':0},
])
def test_manifest_rejects_invalid_source_spans(span):
    doc=manifest();doc['clauses'][0]['source']=span
    with pytest.raises(GovernanceError):parse_manifest(encoded(doc))


def test_manifest_rejects_duplicate_clause_and_fact_ids():
    for section in ('clauses','facts'):
        doc=manifest();doc[section].append(dict(doc[section][0]))
        with pytest.raises(GovernanceError):parse_manifest(encoded(doc))


def test_manifest_rejects_invalid_bytes_and_unbounded_input():
    for raw in (b'\xff',b'not-json',b'NaN',b' '*(8*1024*1024+1)):
        with pytest.raises(GovernanceError):parse_manifest(raw)


@pytest.mark.parametrize('value',('',None,'A'*64,'0'*63,123))
def test_governance_digest_validation(value):
    with pytest.raises(GovernanceError):_hex(value)
    assert _hex('a'*64)=='a'*64


@pytest.mark.parametrize('value',('',None,' '*12,'a'*513))
def test_governance_string_validation(value):
    with pytest.raises(GovernanceError):_text(value,'field')
    assert _optional_edition(None) is None
    assert _optional_edition('') is None
    assert _optional_edition('rev')=='rev'


def task():
    return {'key':'t1','cabal_id':'c1','role_id':'coder','acceptance_digest':'a'*64,
            'depends_on':[],'resources':{'cpu':1},'duration_units':1,
            'package_digest':'b'*64,'entrypoint_id':'entry','grant_id':'grant'}


def test_task_model_validates_and_normalizes_resources():
    t=task();t['resources']={'memory':3,'cpu':2}
    result=TaskSpec.parse(t)
    assert result.resources==(('cpu',2),('memory',3))
    assert result.depends_on==()
    assert result.priority==0


@pytest.mark.parametrize('field,value',[
    ('key',''),('cabal_id',None),('role_id',13),('acceptance_digest',None),
    ('depends_on','not-list'),('depends_on',['t2','t2']),('depends_on',[None]),
    ('resources',[]),('resources',{'cpu':0}),('resources',{'cpu':True}),
    ('duration_units',0),('duration_units',1.25),('priority',False),
    ('package_digest',''),('entrypoint_id',''),('grant_id',''),
])
def test_task_rejects_incomplete_or_unsafe_assignment(field,value):
    doc=task();doc[field]=value
    with pytest.raises(ValueError):TaskSpec.parse(doc)


def test_task_rejects_extra_fields_and_non_mapping():
    with pytest.raises(ValueError):TaskSpec.parse('bad')
    with pytest.raises(ValueError):TaskSpec.parse({**task(),'arbitrary':'instruction'})


def test_feature_brief_accepts_explicit_authorized_repair():
    doc={'acceptance_digest':'a'*64,'source_revision_id':'rev','tasks':[task()],
         'repair_of_task_id':'old','failure_evidence_digest':'b'*64}
    parsed=FeatureBrief.parse(doc)
    assert parsed.repair_of_task_id=='old'
    assert len(parsed.tasks)==1


@pytest.mark.parametrize('mutation',[
    {'tasks':[]}, {'tasks':{}}, {'acceptance_digest':''}, {'source_revision_id':None},
    {'repair_of_task_id':'task'}, {'failure_evidence_digest':'evidence'},
    {'repair_of_task_id':12,'failure_evidence_digest':'evidence'},
])
def test_feature_brief_invalid_documents_are_rejected(mutation):
    doc={'acceptance_digest':'a'*64,'source_revision_id':'rev','tasks':[task()]}
    doc.update(mutation)
    with pytest.raises(ValueError):FeatureBrief.parse(doc)


def test_feature_brief_rejects_unknown_fields_duplicate_tasks_and_missing_required_fields():
    base={'acceptance_digest':'a'*64,'source_revision_id':'rev','tasks':[task()]}
    for bad in ({**base,'invented':True},{**base,'tasks':[task(),task()]},
                {**base,'tasks':None},{'tasks':[task()]},'bad'):
        with pytest.raises(ValueError):FeatureBrief.parse(bad)