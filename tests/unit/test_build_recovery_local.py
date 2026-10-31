"""The C journal, not external task text, determines CI readback requests."""
import hashlib
import json
import sqlite3
from types import SimpleNamespace

from studio.app.build_recovery import BuildRecoveryPump
from studio.integrations.api_ports import GOperatorPorts
from studio.integrations.ci import BuildObservation
from studio.storage.workforce import WorkforceStorage


class Tx:
    def __init__(self, db):
        self.db = db
    def all(self, sql, args=()):
        cur = self.db.execute(sql,args)
        return [dict(zip([col[0] for col in cur.description],row)) for row in cur.fetchall()]
    def one(self, sql, args=()):
        values=self.all(sql,args)
        return values[0] if values else None


class CStore(WorkforceStorage):
    def __init__(self):
        db=sqlite3.connect(':memory:')
        super().__init__(db)
        db.executescript('''CREATE TABLE ci_runs(project_id TEXT,ci_id TEXT,source_revision_id TEXT,status TEXT);
            CREATE TABLE effect_intents(project_id TEXT,subject_id TEXT,action TEXT,state TEXT,
                                        effect_id TEXT,request_id TEXT);
            CREATE TABLE source_revisions(project_id TEXT,revision_id TEXT,commit_sha TEXT,PRIMARY KEY(project_id,revision_id));
            CREATE TABLE workforce_command_results(project_id TEXT,action_owner TEXT,request_id TEXT,result_json TEXT);
            CREATE TABLE idempotency(project_id TEXT,action_owner TEXT,request_id TEXT,result_digest TEXT,status TEXT);''')
    def read(self, project, fn):
        return fn(Tx(self._connection))
    def add(self, project, suffix=''):
        request='req'+project+suffix
        doc=dict(project_id=project,ci_id='ci'+project+suffix,effect_id='effect'+project+suffix,
                 source_sha='a'*40,target_id='build',intent_id='intent'+project+suffix)
        raw=json.dumps(doc,sort_keys=True,separators=(',',':'))
        db=self._connection
        db.execute('INSERT INTO ci_runs VALUES(?,?,?,?)',(project,doc['ci_id'],'revision','queued'))
        db.execute('INSERT INTO effect_intents VALUES(?,?,?,?,?,?)',
                   (project,doc['ci_id'],'build.dispatch','dispatched',doc['effect_id'],'G.build.'+request))
        db.execute('INSERT OR IGNORE INTO source_revisions VALUES(?,?,?)',(project,'revision',doc['source_sha']))
        db.execute('INSERT INTO workforce_command_results VALUES(?,?,?,?)',
                   (project,'WG-G',request,raw))
        db.execute('INSERT INTO idempotency VALUES(?,?,?,?,?)',
                   (project,'WG-G',request,hashlib.sha256(raw.encode()).hexdigest(),'finished'))
        db.commit()


class Provider(GOperatorPorts):
    def __init__(self, project):
        self.binding=SimpleNamespace(project_id=project)
        self.actions=SimpleNamespace(observe=self.observe)
        self.calls=[]
    def observe(self, **kwargs):
        self.calls.append(kwargs)
        return BuildObservation(123,'a'*40,'completed','success','https://example.com')


def test_builds_are_paged_per_project_and_never_dispatched():
    writer=CStore()
    writer.add('p1');writer.add('p2')
    providers={p:Provider(p) for p in ('p1','p2')}
    pump=BuildRecoveryPump(writer=writer,provider_for_project=providers.get,
                           projects_per_poll=1,builds_per_project=1)
    assert pump.poll_once()=={'observed':1,'unknown':0,'errors':0}
    assert pump.poll_once()=={'observed':1,'unknown':0,'errors':0}
    assert len(pump.repository.observations)==2
    assert all(call['source_sha']=='a'*40 for provider in providers.values()
               for call in provider.calls)


def test_project_failure_isolated_without_redispatch():
    writer=CStore();writer.add('p1');writer.add('p2')
    healthy=Provider('p2')
    def factory(project):
        if project=='p1':
            raise RuntimeError('missing credential')
        return healthy
    pump=BuildRecoveryPump(writer=writer,provider_for_project=factory,projects_per_poll=2)
    assert pump.poll_once()=={'observed':1,'unknown':0,'errors':1}
    assert len(healthy.calls)==1


def test_tampered_c_result_is_not_readback_authority():
    writer=CStore();writer.add('p1')
    writer._connection.execute("UPDATE workforce_command_results SET result_json='{}'")
    writer._connection.commit()
    healthy=Provider('p1')
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:healthy)
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    assert healthy.calls==[]


def test_unresolved_first_build_does_not_starve_later_builds():
    writer=CStore()
    writer.add('p1','a');writer.add('p1','b');writer.add('p1','c')
    provider=Provider('p1')
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:provider,
                           projects_per_poll=1,builds_per_project=1)
    for _ in range(4):
        assert pump.poll_once()=={'observed':1,'unknown':0,'errors':0}
    assert [row[1] for row in pump.repository.observations]==[
        'cip1a','cip1b','cip1c','cip1a']


def test_unobserved_runs_remain_unknown_without_dispatch():
    writer=CStore(); writer.add('p1')
    provider=Provider('p1')
    provider.actions.observe=lambda **kwargs: None
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:provider)
    assert pump.poll_once()=={'observed':0,'unknown':1,'errors':0}
    assert pump.repository.observations[0][2] is None


def test_unbound_project_cannot_read_actions():
    writer=CStore(); writer.add('p1')
    fake=Provider('p2')
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:fake)
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    assert fake.calls==[]
