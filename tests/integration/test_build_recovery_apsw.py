"""Actual APSW build records reconcile missed Actions without redispatching."""
from types import SimpleNamespace
import pytest
apsw=pytest.importorskip('apsw')

from studio.app.build_recovery import BuildRecoveryPump
from studio.integrations.api_ports import GOperatorPorts
from studio.integrations.ci import BuildObservation
from studio.storage.builds import BuildAPSWRepository
from studio.storage.workforce import WorkforceStorage


class ObservedActions:
    def __init__(self):
        self.ready=False
        self.calls=[]
        self.direct_calls=[]

    def observe(self, *, project_id, target_id, source_sha, intent_id):
        self.calls.append((project_id, target_id, source_sha, intent_id))
        if not self.ready:
            return None
        return BuildObservation(911,source_sha,'in_progress',None,'https://example.com/run/911')

    def verify_run(self, *, project_id, target_id, intent_id, run_id, expected_sha):
        self.direct_calls.append((project_id,target_id,intent_id,run_id,expected_sha))
        return BuildObservation(run_id,expected_sha,'completed','success','https://example.com/run/911')


class BoundProvider(GOperatorPorts):
    def __init__(self, project_id, actions):
        self.binding=SimpleNamespace(project_id=project_id)
        self.actions=actions


def test_missed_ci_notification_recovered_from_original_c_intent(tmp_path):
    writer=WorkforceStorage.open(tmp_path/'studio.sqlite',fresh=True)
    try:
        db=writer._connection
        db.execute("INSERT INTO projects(project_id,name,charter_digest,state) "
                   "VALUES('p1','Project','digest','active')")
        db.execute("INSERT INTO project_repositories VALUES('p1','repo','github','main',NULL)")
        db.execute("INSERT INTO source_revisions VALUES(?,?,?,?,?,'canonical')",
                   ('p1','revision','repo','a'*40,'tree'))
        store=BuildAPSWRepository(writer)
        prepared=store.prepare(project_id='p1',request_id='request',
                               source_revision_id='revision',build_target='compile')
        assert store.claim_first_dispatch(project_id='p1',effect_id=prepared.effect_id)
        actions=ObservedActions()
        providers=[]
        def resolve(project_id):
            providers.append(project_id)
            return BoundProvider(project_id,actions)
        monotonic_now=[1000.0]
        pump=BuildRecoveryPump(writer=writer,provider_for_project=resolve,
                               projects_per_poll=1,builds_per_project=1,
                               clock=lambda:monotonic_now[0])
        assert pump.poll_once()=={'observed':0,'unknown':1,'errors':0}
        assert writer.read('p1',lambda tx:tx.one(
            'SELECT status FROM ci_runs WHERE project_id=? AND ci_id=?',
            ('p1',prepared.ci_id)))=={'status':'unknown'}
        actions.ready=True
        # The first missing readback applies a 30-second retry window.
        assert pump.poll_once()=={'observed':0,'unknown':0,'errors':0}
        monotonic_now[0]+=30.0
        assert pump.poll_once()=={'observed':1,'unknown':0,'errors':0}
        assert writer.read('p1',lambda tx:tx.one(
            'SELECT status,remote_run_id FROM ci_runs WHERE project_id=? AND ci_id=?',
            ('p1',prepared.ci_id)))=={'status':'running','remote_run_id':'911'}
        # A running run uses the next 60-second window before pinned readback.
        assert pump.poll_once()=={'observed':0,'unknown':0,'errors':0}
        monotonic_now[0]+=60.0
        assert pump.poll_once()=={'observed':1,'unknown':0,'errors':0}
        assert writer.read('p1',lambda tx:tx.one(
            'SELECT status,remote_run_id FROM ci_runs WHERE project_id=? AND ci_id=?',
            ('p1',prepared.ci_id)))=={'status':'passed','remote_run_id':'911'}
        assert writer.read('p1',lambda tx:tx.one(
            'SELECT state FROM effect_intents WHERE project_id=? AND effect_id=?',
            ('p1',prepared.effect_id)))=={'state':'observed'}
        assert pump.poll_once()=={'observed':0,'unknown':0,'errors':0}
        assert providers==['p1','p1','p1']
        assert actions.calls==[('p1','compile','a'*40,prepared.intent_id)]*2
        assert actions.direct_calls==[('p1','compile',prepared.intent_id,911,'a'*40)]
    finally:
        writer.close()