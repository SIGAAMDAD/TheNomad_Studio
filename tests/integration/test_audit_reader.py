"""Actual APSW readback and J page contract, not an in-memory audit fake."""
import pytest

apsw = pytest.importorskip('apsw')

from studio.api.events import EventPageService
from studio.api.errors import ApiError
from studio.storage.audit import ProjectAuditReader
from studio.storage.engine import ScopedStore
from studio.storage.errors import Invalid


@pytest.fixture
def setup(tmp_path):
    store = ScopedStore(tmp_path/'audit.sqlite', fresh=True)
    try:
        # Initial fixture only: production must use C's authenticated writes.
        store._conn.execute(
            "INSERT INTO principals(principal_id,auth_issuer,auth_subject,kind) "
            "VALUES('p','test','p','human')")
        for project in ('p1','p2'):
            store._conn.execute(
                'INSERT INTO projects(project_id,name,charter_digest,state) VALUES(?,?,?,?)',
                (project,project,'digest','active'))
        for project in ('p1','p2'):
            for n in range(1,521):
                store._conn.execute(
                    'INSERT INTO audit_events(project_id,event_seq,event_type,subject_id,'
                    'payload_digest,actor_principal_id) VALUES(?,?,?,?,?,?)',
                    (project,n,'source.committed','s-'+str(n),'d'*64,'p'))
        yield store, ProjectAuditReader(store)
    finally:
        store.close()


def test_indexed_project_page_cursor_and_boundary(setup):
    store, reader = setup
    service = EventPageService(reader)
    actor = {'allowed_project_ids':['p1']}
    first = service.listEvents(actor,'p1',after=0,limit=500)
    assert [event['event_seq'] for event in first['events']] == list(range(1,501))
    assert first['next_cursor'] == 500
    last = service.listEvents(actor,'p1',after=500,limit=500)
    assert [event['event_seq'] for event in last['events']] == list(range(501,521))
    assert last['next_cursor'] == 520
    assert service.listEvents(actor,'p1',after=520,limit=500)['next_cursor'] == 520
    with pytest.raises(ApiError,match='project unavailable'):
        service.listEvents(actor,'p2')
    assert {x['project_id'] for x in reader.scan_project_events(
        project_id='p1',after=400,limit=500)} == {'p1'}
    assert store._conn.execute(
        "EXPLAIN QUERY PLAN SELECT event_seq FROM audit_events INDEXED BY idx_events_cursor "
        "WHERE project_id=? AND event_seq>? ORDER BY event_seq LIMIT ?",
        ('p1',400,100)).fetchone() is not None


@pytest.mark.parametrize('after,limit', [(-1,1),(0,0),(0,501),(1.0,2),(True,1),(0,True)])
def test_reader_rejects_bad_cursor_limits(setup, after, limit):
    reader=setup[1]
    with pytest.raises(Invalid):
        reader.scan_project_events(project_id='p1',after=after,limit=limit)
