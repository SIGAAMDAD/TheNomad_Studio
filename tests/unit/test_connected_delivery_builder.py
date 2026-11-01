"""Connected command publisher retains the authoritative C/G/I owner boundary."""
from types import SimpleNamespace
import sqlite3
import weakref

import pytest

import studio.app.connected_delivery as module
from studio.app.connected_delivery import ConnectedGitHubCommandDeliveryBuilder
from studio.integrations.common import ProjectBinding
from studio.integrations.github import GitHub, GitHubChannel
from studio.storage.workforce import WorkforceStorage
from studio.workforce import Workforce


class NoNetworkHTTP:
    def request(self, *args, **kwargs):
        raise AssertionError("no network request should occur while assembling a publisher")


class Worker(Workforce):
    def publish_pending(self, actor, project_id, *, provider, limit=100):
        assert actor == {"project_id": project_id}
        assert provider.channel.project_id == project_id
        assert limit == 2
        return {"published": 1, "unknown": 0, "rejected": 0}


def test_builder_reuses_single_writer_and_verified_per_project_transport(monkeypatch, tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE command_outbox(project_id TEXT,state TEXT)")
    conn.execute("INSERT INTO command_outbox VALUES('project','queued')")
    store = WorkforceStorage(conn)
    weakref.finalize(store, conn.close)
    workers = Worker(store, require_action=lambda *_: None)
    captured = []
    def providers(*, writer, artifact_root, projects, credential_resolver, http):
        captured.append((writer, artifact_root, projects, credential_resolver, http))
        def get(project_id):
            github = GitHub(NoNetworkHTTP(), lambda: "token",
                            GitHubChannel(project_id, "owner", "repo", "comms"),
                            ProjectBinding(project_id, "account"))
            return SimpleNamespace(relay=github,
                                   actions=SimpleNamespace(github=None))
        return get
    monkeypatch.setattr(module, "ConnectedGitHubProviders", providers)
    resolve = lambda key: "token"
    builder = ConnectedGitHubCommandDeliveryBuilder(
        projects=(), credential_resolver=resolve,
        actor_for_project=lambda project: {"project_id": project},
        commands_per_project=2)
    pump = builder.for_writer(store, tmp_path, workers)
    assert captured[0][0] is store
    assert captured[0][3] is resolve
    assert pump.poll_once() == {"published": 1, "unknown": 0, "rejected": 0}


def test_builder_rejects_missing_actor_resolver():
    with pytest.raises(TypeError, match="actor"):
        ConnectedGitHubCommandDeliveryBuilder(
            projects=(), credential_resolver=lambda _: "token",
            actor_for_project=None)


def test_installed_shared_provider_is_used_without_second_rate_governor(monkeypatch, tmp_path):
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE command_outbox(project_id TEXT,state TEXT)')
    conn.execute("INSERT INTO command_outbox VALUES('project','queued')")
    store=WorkforceStorage(conn)
    weakref.finalize(store,conn.close)
    worker=Worker(store,require_action=lambda *_:None)
    def unexpected(**kwargs):
        raise AssertionError('second G provider/rate governor instantiated')
    monkeypatch.setattr(module,'ConnectedGitHubProviders',unexpected)
    calls=[]
    def shared(project_id):
        calls.append(project_id)
        github=GitHub(NoNetworkHTTP(),lambda: 'token',
                      GitHubChannel(project_id,'owner','repo','comms'),
                      ProjectBinding(project_id,'account'))
        return SimpleNamespace(relay=github)
    builder=ConnectedGitHubCommandDeliveryBuilder(
        projects=(),credential_resolver=lambda _: 'token',
        actor_for_project=lambda project: {'project_id':project},commands_per_project=2)
    publisher=builder.for_writer(store,tmp_path,worker,provider_for_project=shared)
    assert publisher.poll_once()=={'published':1,'unknown':0,'rejected':0}
    assert calls==['project']


def test_shared_provider_requires_callable(tmp_path):
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE command_outbox(project_id TEXT,state TEXT)')
    store=WorkforceStorage(conn)
    weakref.finalize(store,conn.close)
    builder=ConnectedGitHubCommandDeliveryBuilder(
        projects=(),credential_resolver=lambda _: 'token',
        actor_for_project=lambda _: {'project_id':'project'})
    with pytest.raises(TypeError,match='callable'):
        builder.for_writer(store,tmp_path,Worker(store,require_action=lambda *_:None),
                           provider_for_project=object())
