"""Real C/APSW governance boundaries: rejected requests leave no authoritative effect."""
import pytest
apsw=pytest.importorskip('apsw')

from studio.governance.model import GovernanceError
from studio.governance.service import digest
from studio.storage.governance import GovernanceAPSWService
from studio.storage.workforce import WorkforceStorage


class BytesNotStaged:
    def read_verified(self,*args):
        raise AssertionError('unverified Bible must fail before G byte access')


class ImpactCatalog:
    def referenced_tasks(self,*args):
        return set()


def actor(*,issuer='test',member=True,owner=True,principal='owner'):
    return {'principal_id':principal,'issuer':issuer,'trusted_proof_ref':'proof',
            'allowed_project_ids':['p'] if member else ['q'], 'owner_verified':owner}


@pytest.fixture
def governance(tmp_path):
    writer=WorkforceStorage.open(tmp_path/'governance.sqlite',fresh=True)
    c=writer._connection
    c.execute("INSERT INTO principals VALUES('owner','test','owner','human')")
    c.execute("INSERT INTO principals VALUES('member','test','member','human')")
    c.execute("INSERT INTO projects(project_id,name,charter_digest,state) "
              "VALUES('p','Project','digest','active')")
    c.execute("INSERT INTO project_grants VALUES('p','owner','owner')")
    c.execute("INSERT INTO project_grants VALUES('p','member','member')")
    svc=GovernanceAPSWService(writer,blobs=BytesNotStaged(),impacts=ImpactCatalog())
    try:
        yield svc,writer
    finally:
        writer.close()


def test_missing_canon_policy_and_stage_fail_closed(governance):
    svc,writer=governance
    with pytest.raises(GovernanceError,match='policy'):
        svc.getProjectPolicy(actor(),'p')
    with pytest.raises(GovernanceError,match='Bible'):
        svc.diffBible(actor(),'p','missing')
    with pytest.raises(GovernanceError,match='Bible'):
        svc.stageBible(actor(),'p','missing',{'request_id':'no-bible'})
    with pytest.raises(GovernanceError,match='publication'):
        svc.publishBible(actor(),'p','missing',{'request_id':'not-published'})


@pytest.mark.parametrize('bad_actor',[
    None, {}, {'principal_id':'owner'}, actor(issuer='unknown'),
    actor(member=False),actor(principal='ghost'),
])
def test_unverified_current_actor_rejected(governance,bad_actor):
    svc,writer=governance
    with pytest.raises(GovernanceError):
        svc.listBibles(bad_actor,'p')
    with pytest.raises(GovernanceError):
        svc.getProjectPolicy(bad_actor,'p')


def test_unverified_owner_cannot_modify_policy_or_grants(governance):
    svc,writer=governance
    req={'request_id':'policy1','policy_digest':digest([]),'rules':[],'expected_revision':0}
    with pytest.raises(GovernanceError,match='owner'):
        svc.setProjectPolicy(actor(owner=False),'p',req)
    with pytest.raises(GovernanceError,match='grant'):
        svc.setProjectPolicy(actor(principal='member'),'p',req)
    with pytest.raises(GovernanceError,match='owner'):
        svc.grantProjectAccess(actor(owner=False),'p',{'request_id':'grant1','principal_id':'member','grant_code':'owner'})


@pytest.mark.parametrize('rules,expected,sha',[
    (None,'list',None),
    ([12],'rule',None),
    ([{'rule_id':'a','subject_type':'bible','action':'read','scope_digest':'a'*64,'disposition':'delegated'}]*2,'duplicate',None),
    ([{'rule_id':'a','subject_type':'bible','action':'read','scope_digest':'a'*64,'disposition':'invalid'}],'disposition',None),
    ([], 'digest', '0'*64),
])
def test_invalid_policy_does_not_change_authority(governance,rules,expected,sha):
    svc,writer=governance
    request={'request_id':'policy1','expected_revision':0,'rules':rules,
             'policy_digest':sha if sha else digest(rules)}
    with pytest.raises(GovernanceError,match=expected):
        svc.setProjectPolicy(actor(),'p',request)
    assert writer.read('p',lambda tx:tx.one(
        'SELECT COUNT(*) AS n FROM project_policies WHERE project_id=?',('p',)))['n']==0


def test_decision_and_grant_negative_transitions(governance):
    svc,writer=governance
    created=svc.createBible(actor(),'p',{'request_id':'b1','kind':'design','title':'Design'})
    with pytest.raises(GovernanceError,match='kind'):
        svc.createBible(actor(),'p',{'request_id':'b2','kind':'unsupported','title':'Invalid'})
    with pytest.raises(GovernanceError,match='subject'):
        svc.requestDecision(actor(),'p',{'request_id':'d-invalid','subject_type':'unknown',
            'subject_id':created['bible_id'],'action':'read','effect_digest':'e'*64})
    with pytest.raises(GovernanceError,match='not found'):
        svc.requestDecision(actor(),'p',{'request_id':'d-missing','subject_type':'bible',
            'subject_id':'wrong','action':'read','effect_digest':'e'*64})
    with pytest.raises(GovernanceError,match='proposal'):
        svc.resolveDecision(actor(),'p','missing',{'request_id':'resolve','decision':'approve','expected_revision':1})
    with pytest.raises(GovernanceError,match='last grant'):
        svc.revokeProjectAccess(actor(),'p',{'request_id':'self','principal_id':'owner','grant_code':'owner'})
    with pytest.raises(GovernanceError,match='does not exist'):
        svc.revokeProjectAccess(actor(),'p',{'request_id':'absent','principal_id':'member','grant_code':'owner'})
    with pytest.raises(GovernanceError,match='not registered'):
        svc.grantProjectAccess(actor(),'p',{'request_id':'ghost','principal_id':'ghost','grant_code':'member'})


def test_decision_revision_effect_and_resolution_replays(governance):
    svc,writer=governance
    item=svc.createBible(actor(),'p',{'request_id':'bible','kind':'design','title':'Design'})
    request={'request_id':'decision','subject_type':'bible','subject_id':item['bible_id'],
             'action':'bible.publish','effect_digest':'e'*64}
    proposed=svc.requestDecision(actor(),'p',request)
    did=proposed['decision_id']
    for attempt,pattern in [
        ({'request_id':'rev','expected_revision':22,'expected_effect_digest':'e'*64,'decision':'approve'},'revision'),
        ({'request_id':'sha','expected_revision':1,'expected_effect_digest':'0'*64,'decision':'approve'},'effect'),
        ({'request_id':'mode','expected_revision':1,'expected_effect_digest':'e'*64,'decision':'invalid'},'resolution'),
        ({'request_id':'revoke','expected_revision':1,'expected_effect_digest':'e'*64,'decision':'revoke'},'revoked'),
    ]:
        with pytest.raises(GovernanceError,match=pattern):
            svc.resolveDecision(actor(),'p',did,attempt)
    rejected=svc.resolveDecision(actor(),'p',did,{
        'request_id':'reject','expected_revision':1,'expected_effect_digest':'e'*64,'decision':'reject'})
    assert rejected['state']=='rejected'
    with pytest.raises(GovernanceError,match='resolved'):
        svc.resolveDecision(actor(),'p',did,{
        'request_id':'duplicate','expected_revision':2,'expected_effect_digest':'e'*64,'decision':'approve'})
    assert svc.listDecisions(actor(),'p')['decisions'][0]['state']=='rejected'