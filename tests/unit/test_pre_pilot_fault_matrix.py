"""Pre-pilot L1 failure injection. Fake C/G fixtures are NOT a live host acceptance test.

A full APSW + production-composition + browser-host run remains separately gated.
"""
import hashlib
import hmac
import io
import json
import math
import threading
import uuid

import pytest

import studio.app.build_recovery as recovery_module
from studio.app.build_recovery import BuildRecoveryPump
from studio.app.github_webhooks import GitHubWebhookBinding, GitHubWebhookHints
from studio.integrations.common import ProviderFailure

from test_build_recovery_local import CStore, Provider
from test_command_delivery_pump import make_pump
from test_github_webhook_hints import rig, request


class RecordingBuildRepository:
    def __init__(self, writer):
        self.observations=[]

    def observe(self, *, project_id, ci_id, run):
        self.observations.append((project_id,ci_id,run))


@pytest.fixture(autouse=True)
def isolate_ci_unit_repository(monkeypatch):
    # No APSW/production repository is exercised by these L1 tests.
    # Production APSW integration must run in its own test module.
    monkeypatch.setattr(recovery_module,'BuildAPSWRepository',RecordingBuildRepository)


@pytest.mark.parametrize("population,budget", [(100, 7), (300, 9), (500, 13)])
def test_large_population_command_fairness(population, budget):
    pump, worker = make_pump(population, projects_per_poll=budget)
    for _ in range(math.ceil(population / budget)):
        observed = pump.poll_once()
        assert 0 <= observed['published'] <= budget
    first = [project for project, _ in worker.calls[:population]]
    assert len(first) == population
    assert len(set(first)) == population
    assert all(limit == 2 for _, limit in worker.calls)
    # No loop iteration is allowed to exceed its configured project budget.
    assert len(worker.calls) == math.ceil(population / budget) * budget


@pytest.mark.parametrize("population", [100, 300, 500])
def test_builds_recover_fairly_across_many_projects(population):
    writer = CStore()
    for i in range(population):
        writer.add(f'p{i:04d}')
    fake = {f'p{i:04d}': Provider(f'p{i:04d}') for i in range(population)}
    pump = BuildRecoveryPump(writer=writer, provider_for_project=fake.get,
                             projects_per_poll=11, builds_per_project=1)
    for _ in range(math.ceil(population / 11)):
        outcome = pump.poll_once()
        assert outcome['errors'] == 0
        assert outcome['observed'] <= 11
    # The L1 recording repository deliberately does not transition builds
    # to terminal status; a final partial page can revisit earlier records.
    assert population <= len(pump.repository.observations) <= math.ceil(population / 11) * 11
    assert len({project for project, _, _ in pump.repository.observations}) == population
    assert all(len(p.calls) >= 1 for p in fake.values())


@pytest.mark.parametrize("bad_content_length,expected", [('',400), ('-1',400),
                                                        ('NaN',400), ('1.5',400),
                                                        (str(2**32),413)])
def test_webhook_malformed_body_length_fails_before_database(bad_content_length, expected):
    writer, receiver, wake = rig()
    status=[]
    env={'REQUEST_METHOD':'POST', 'PATH_INFO':'/webhooks/github',
         'CONTENT_LENGTH':bad_content_length, 'CONTENT_TYPE':'application/json',
         'HTTP_X_HUB_SIGNATURE_256':'sha256='+'0'*64,
         'HTTP_X_GITHUB_DELIVERY':str(uuid.uuid4()),
         'HTTP_X_GITHUB_EVENT':'push', 'wsgi.input':io.BytesIO(b'{}')}
    assert receiver(env, lambda code, headers: status.append(int(code[:3]))) == [b'']
    assert status == [expected]
    assert writer.events == {}
    assert wake == []


@pytest.mark.parametrize("body", [b'{notjson', b'{"a":1,"a":2}', b'NaN',
                                    b'[]', b'null', b'{"x":NaN}', b'\xff'])
def test_webhook_json_adversarial_inputs_do_not_mutate(body):
    writer, receiver, wake=rig()
    sign='sha256='+hmac.new(b'webhook-secret-value',body,hashlib.sha256).hexdigest()
    status=[]
    env={'REQUEST_METHOD':'POST','PATH_INFO':'/webhooks/github',
         'CONTENT_LENGTH':str(len(body)),'CONTENT_TYPE':'application/json',
         'HTTP_X_GITHUB_EVENT':'push','HTTP_X_GITHUB_DELIVERY':str(uuid.uuid4()),
         'HTTP_X_HUB_SIGNATURE_256':sign,'wsgi.input':io.BytesIO(body)}
    assert receiver(env,lambda code,headers:status.append(int(code[:3])))==[b'']
    assert status == [400]
    assert not writer.events
    assert not wake


def test_same_webhook_delivery_many_repetitions_remains_one_durable_record():
    writer, receiver, wake = rig()
    for _ in range(100):
        assert request(receiver, {}) == 202
    assert len(writer.events) == 1
    assert len(wake) == 100  # hints may be coalesced by consumer, no duplicated mutation


def test_distinct_project_with_same_webhook_guid_is_independently_scoped():
    from test_github_webhook_hints import RecordingC
    writer=RecordingC()
    shared_secret=b'webhook-secret-value'
    bindings=tuple(GitHubWebhookBinding(pid,'owner','repo','main','studio/comms',shared_secret)
                   for pid in ('p1','p2'))
    wake=[]
    receiver=GitHubWebhookHints(writer,bindings,lambda:wake.append('wake'))
    assert request(receiver,{})==202
    assert set(project for project, request_id in writer.events)=={'p1','p2'}
    assert len(writer.events)==2
    assert wake==['wake']


def test_readback_provider_failure_does_not_authenticate_every_cycle():
    now=[100.]
    writer=CStore(); writer.add('p1')
    calls=[]
    def unavailable(project):
        calls.append(project)
        raise ProviderFailure('throttled',status=429,retry_after=90.)
    pump=BuildRecoveryPump(writer=writer,provider_for_project=unavailable,
                           clock=lambda:now[0],interval_seconds=10)
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    for _ in range(20):
        assert pump.poll_once()=={'observed':0,'unknown':0,'errors':0}
    assert calls==['p1']
    now[0]+=89
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':0}
    now[0]+=1
    assert pump.poll_once()=={'observed':0,'unknown':0,'errors':1}
    assert calls==['p1','p1']


def test_provider_backoff_one_project_does_not_starve_another():
    now=[0.]
    writer=CStore(); writer.add('p1');writer.add('p2')
    healthy=Provider('p2');calls=[]
    def resolver(project):
        calls.append(project)
        if project=='p1':
            raise ProviderFailure('quota',status=429,retry_after=600.)
        return healthy
    pump=BuildRecoveryPump(writer=writer,provider_for_project=resolver,
                           clock=lambda:now[0],interval_seconds=10,projects_per_poll=2)
    assert pump.poll_once()=={'observed':1,'unknown':0,'errors':1}
    assert pump.poll_once()=={'observed':1,'unknown':0,'errors':0}
    assert calls==['p1','p2','p2']


def test_large_retry_after_cannot_produce_nonfinite_deadline():
    writer=CStore();writer.add('p1')
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:Provider('p1'),
                           clock=lambda:1000.,interval_seconds=10,
                           max_readback_delay_seconds=3600)
    pump._defer('p1','ci',1e308,pump._hint_epoch)
    with pump._schedule_lock:
        deadline, attempts = pump._readback_schedule['p1','ci']
    assert math.isfinite(deadline)
    assert 10 <= deadline-1000 <= 86400
    assert attempts==1


def test_webhook_hint_inflight_provider_result_cannot_restore_stale_backoff():
    now=[5.]
    writer=CStore();writer.add('p1')
    provider=Provider('p1')
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:provider,
                           clock=lambda:now[0],interval_seconds=10)
    entered=threading.Event(); release=threading.Event()
    def slow(**kwargs):
        entered.set()
        assert release.wait(5)
        return None
    provider.actions.observe=slow
    def signal():
        assert entered.wait(5)
        pump.request_reconciliation()
        release.set()
    t=threading.Thread(target=signal)
    t.start()
    result=pump.poll_once()  # sqlite3 C fake is owned by this thread
    t.join(5)
    assert not t.is_alive()
    assert result=={'observed':0,'unknown':1,'errors':0}
    assert pump._readback_schedule=={}
    provider.actions.observe=lambda **kwargs:None
    assert pump.poll_once()=={'observed':0,'unknown':1,'errors':0}


def test_pending_schedule_memory_is_bounded_under_many_unresolved_builds():
    writer=CStore();writer.add('p1')
    pump=BuildRecoveryPump(writer=writer,provider_for_project=lambda _:Provider('p1'))
    for index in range(20100):
        pump._defer('p1',f'ci_{index}',None,pump._hint_epoch)
    assert len(pump._readback_schedule)==20000
    assert ('p1','ci_0') not in pump._readback_schedule
    assert ('p1','ci_20099') in pump._readback_schedule
