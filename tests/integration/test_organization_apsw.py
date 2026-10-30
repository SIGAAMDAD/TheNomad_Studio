"""Real APSW transactions for B cabals, with duplicate, rollback and scope tests."""
from types import SimpleNamespace

import pytest

apsw = pytest.importorskip('apsw')

from studio.organization.service import OrganizationService, TrustedActor
from studio.organization.verification import OrganizationError
from studio.storage.organization import OrganizationAPSWStore
from studio.storage.workforce import WorkforceStorage


@pytest.fixture
def context(tmp_path):
    writer=WorkforceStorage.open(tmp_path/'org.sqlite',fresh=True)
    conn=writer._connection
    conn.execute("INSERT INTO principals VALUES('owner','test','owner','human')")
    for project in ('p1','p2'):
        conn.execute('INSERT INTO projects(project_id,name,charter_digest,state) VALUES(?,?,?,?)',
                     (project,project,'digest','active'))
    service=OrganizationService(
        OrganizationAPSWStore(writer), policy=SimpleNamespace(),
        source=SimpleNamespace(), trust=SimpleNamespace())
    actor=TrustedActor('owner',frozenset({'p1'}),frozenset({'p1'}),'proof')
    yield writer,service,actor
    writer.close()


def test_cabal_is_durable_and_idempotent_with_original_result(context):
    writer,svc,actor=context
    initial={'request_id':'r1','name':'Wasteland','charter_digest':'a'*64}
    first=svc.createCabal(actor,'p1',**initial)
    assert first['revision']==1
    assert svc.createCabal(actor,'p1',**initial)==first
    cabals=svc.listCabals(actor,'p1')['cabals']
    assert cabals==[{'cabal_id':first['cabal_id'],'name':'Wasteland','state':'active'}]
    assert writer.read('p1',lambda tx:tx.one(
        'SELECT COUNT(*) AS n FROM cabals WHERE project_id=?',('p1',)))['n']==1
    with pytest.raises(OrganizationError,match='request ID reused'):
        svc.createCabal(actor,'p1',**{**initial,'name':'Different'})
    assert writer.read('p1',lambda tx:tx.one(
        'SELECT COUNT(*) AS n FROM cabals WHERE project_id=?',('p1',)))['n']==1


def test_c_rejects_cross_project_and_forbidden_table(context):
    writer,svc,actor=context
    store=svc.store
    with pytest.raises(OrganizationError,match='cross-project'):
        store.read('p1',lambda tx:tx.many('cabals',project_id='p2'))
    with pytest.raises(OrganizationError,match='not owned'):
        store.read('p1',lambda tx:tx.many('principals'))
    assert store.read('p1',lambda tx:tx.many('cabals'))==[]


def test_atomic_rollback_when_delegated_mutation_fails(context):
    writer,svc,actor=context
    store=svc.store
    def bad(tx):
        tx.insert('cabals',{'project_id':'p1','cabal_id':'bad',
                            'name':'bad','charter_digest':'a'*64,'state':'active'})
        raise OrganizationError('conflict','forced rollback')
    with pytest.raises(OrganizationError,match='forced rollback'):
        store.mutate(project_id='p1',principal_id='owner',action_name='B.test',
                     request_id='rollback',payload_digest='a'*64,action=bad)
    assert writer.read('p1',lambda tx:tx.one(
        'SELECT 1 AS n FROM cabals WHERE project_id=? AND cabal_id=?',
        ('p1','bad'))) is None
