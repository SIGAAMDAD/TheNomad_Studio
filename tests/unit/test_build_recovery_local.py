"""The C journal, not external task text, determines CI readback requests."""
import hashlib
import json
import sqlite3
import weakref
from types import SimpleNamespace
import pytest

import studio.app.build_recovery as module
from studio.app.build_recovery import BuildRecoveryPump
from studio.integrations.api_ports import GOperatorPorts
from studio.integrations.ci import BuildObservation
from studio.storage.workforce import WorkforceStorage


class RecordingBuildRepository:
    def __init__(self, writer):
        self.observations=[]
    def observe(self, *, project_id, ci_id, run):
        self.observations.append((project_id,ci_id,run))


@pytest.fixture(autouse=True)
def isolate_build_repository(monkeypatch):
    monkeypatch.setattr(module,'BuildAPSWRepository',RecordingBuildRepository)


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
        # Reference test stores also serve the cross-module pre-pilot fixtures.
        # Close their SQLite handle when the last writer owner is collected.
        weakref.finalize(self, db.close)
        db.executescript('''CREATE TABLE ci_runs(project_id TEXT,ci_id TEXT,source_revision_id TEXT,status TEXT,remote_run_id TEXT);
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
        db.execute('INSERT INTO ci_runs VALUES(?,?,?,?,?)',(project,doc['ci_id'],'revision','queued',''))
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


def test_previously_verified_run_id_uses_direct_pinned_readback():
    writer=CStore(); writer.add('p1')
    writer._connection.execute("UPDATE ci_runs SET status='running',remote_run_id='911'")
    provider=Provider('p1')
    provider.actions.observe=lambda **kwargs: (_ for _ in ()).throw(AssertionError('list scan forbidden'))
    calls=[]
    def verified(**kwargs):
        calls.append(kwargs)
        return BuildObservation(911,'a'*40,'completed','success','url')
    provider.actions.verify_run=verified
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:provider)
    assert pump.poll_once()=={'observed':1,'unknown':0,'errors':0}
    assert calls==[dict(project_id='p1',target_id='build',intent_id='intentp1',
                       run_id=911,expected_sha='a'*40)]


def test_invalid_persisted_run_id_blocks_provider_readback():
    writer=CStore();writer.add('p1')
    writer._connection.execute("UPDATE ci_runs SET status='running',remote_run_id='bad-id'")
    provider=Provider('p1')
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:provider)
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    assert provider.calls==[]


def test_unresolved_readback_uses_exponential_local_backoff_without_reauthenticating():
    now=[1000.0]
    writer=CStore();writer.add('p1')
    provider=Provider('p1')
    provider.actions.observe=lambda **kwargs: None
    factory_calls=[]
    def resolver(project):
        factory_calls.append(project)
        return provider
    pump=BuildRecoveryPump(writer=writer,provider_for_project=resolver,
                           interval_seconds=10, max_readback_delay_seconds=80,
                           clock=lambda: now[0])
    assert pump.poll_once()=={'observed':0,'unknown':1,'errors':0}
    for _ in range(5):
        assert pump.poll_once()=={'observed':0,'unknown':0,'errors':0}
    assert factory_calls==['p1']
    now[0]+=10
    assert pump.poll_once()=={'observed':0,'unknown':1,'errors':0}
    now[0]+=19
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':0}
    now[0]+=1
    assert pump.poll_once()=={'observed':0,'unknown':1,'errors':0}
    assert factory_calls==['p1']*3
    # A signed event supplies a hint; the next cycle checks immediately.
    pump.request_reconciliation()
    assert pump.poll_once()=={'observed':0,'unknown':1,'errors':0}
    assert len(factory_calls)==4


def test_webhook_during_slow_remote_response_cannot_be_overridden_by_old_deferral():
    now=[1000.0]
    writer=CStore();writer.add('p1')
    provider=Provider('p1')
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _: provider,
                           interval_seconds=30,clock=lambda: now[0])
    def stale_response(**kwargs):
        pump.request_reconciliation()
        return None
    provider.actions.observe=stale_response
    assert pump.poll_once()=={'observed':0,'unknown':1,'errors':0}
    # The previous read completed after the webhook; do not swallow it.
    provider.actions.observe=lambda **kwargs: None
    assert pump.poll_once()=={'observed':0,'unknown':1,'errors':0}


def test_rate_limit_header_cooldown_is_respected_without_new_network_lookups():
    now=[1000.0]
    writer=CStore();writer.add('p1')
    provider=Provider('p1')
    class QuotaError(Exception):
        retry_after=180.0
    calls=[]
    def unavailable(**kwargs):
        calls.append(kwargs)
        raise QuotaError()
    provider.actions.observe=unavailable
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:provider,
                           interval_seconds=10,clock=lambda:now[0])
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    now[0]+=179
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':0}
    assert len(calls)==1
    now[0]+=1
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    assert len(calls)==2


def test_invalid_readback_backoff_configuration_rejected():
    import pytest
    writer=CStore()
    for params in ({'max_readback_delay_seconds':0},
                   {'max_readback_delay_seconds':float('nan')},
                   {'clock':None}):
        with pytest.raises(ValueError):
            BuildRecoveryPump(writer=writer,provider_for_project=lambda _:None,**params)


def test_provider_credential_failure_uses_same_backoff_budget():
    now=[1000.0]
    writer=CStore(); writer.add('p1')
    calls=[]
    def failed_resolver(project):
        calls.append(project)
        raise RuntimeError('credential provider down')
    pump=BuildRecoveryPump(writer=writer,provider_for_project=failed_resolver,
                           interval_seconds=10,clock=lambda:now[0])
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    for _ in range(7):
        assert pump.poll_once()=={'observed':0,'unknown':0,'errors':0}
    assert calls==['p1']
    now[0]+=10
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    assert len(calls)==2
    pump.request_reconciliation()
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    assert len(calls)==3


def test_existing_slow_poll_configuration_remains_supported():
    writer=CStore()
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:None,
                           interval_seconds=3600)
    assert pump.max_readback_delay_seconds>=3600


def test_reference_store_connection_is_closed_on_finalization():
    import gc
    writer = CStore()
    db = writer._connection
    reference = weakref.ref(writer)
    del writer
    gc.collect()
    assert reference() is None
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')
