"""Bounded and fair polling must not lose ready projects or replay by itself."""
import sqlite3
import weakref

import pytest

from studio.app.command_delivery import CommandDeliveryPump, CommandDeliveryPumpBuilder
from studio.storage.workforce import WorkforceStorage
from studio.workforce import Workforce


class CountingWorkforce(Workforce):
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def publish_pending(self, actor, project_id, *, provider, limit=100):
        assert actor["project"] == project_id
        assert provider == project_id
        self.calls.append((project_id, limit))
        if project_id in self.fail:
            raise RuntimeError("project provider unavailable")
        return {"published": 1, "unknown": 0, "rejected": 0}


def make_pump(count=23, *, projects_per_poll=4, commands_per_project=2, fail=()):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE command_outbox(project_id TEXT, state TEXT)")
    connection.executemany(
        "INSERT INTO command_outbox VALUES(?, 'queued')",
        [(f"project-{i:03d}",) for i in range(count)])
    store = WorkforceStorage(connection)
    weakref.finalize(store, connection.close)
    worker = CountingWorkforce(fail=fail)
    pump = CommandDeliveryPump(
        writer=store, workforce=worker,
        actor_for_project=lambda project_id: {"project": project_id},
        github_for_project=lambda project_id: project_id,
        projects_per_poll=projects_per_poll,
        commands_per_project=commands_per_project)
    return pump, worker


def test_more_projects_than_scan_limit_are_eventually_processed():
    pump, worker = make_pump(23)
    for _ in range(6):
        result = pump.poll_once()
        assert result["published"] <= 4
    assert len(worker.calls) == 24
    assert len(set(project for project, _ in worker.calls[:23])) == 23
    assert set(project for project, _ in worker.calls) == {
        f"project-{i:03d}" for i in range(23)}
    assert all(limit == 2 for _, limit in worker.calls)
    assert pump.poll_once()["published"] == 4
    assert worker.calls[23][0] == "project-000"


def test_project_failure_isolated_and_cursor_still_advances():
    pump, worker = make_pump(6, projects_per_poll=3, fail={"project-001"})
    assert pump.poll_once() == {"published": 2, "unknown": 0, "rejected": 0}
    assert isinstance(pump.last_error, RuntimeError)
    assert pump.poll_once() == {"published": 3, "unknown": 0, "rejected": 0}
    assert [project for project, _ in worker.calls] == [
        f"project-{i:03d}" for i in range(6)]
    assert pump.last_error is None


def test_new_project_below_cursor_is_reached_on_wrap():
    pump, worker = make_pump(4, projects_per_poll=2)
    pump.poll_once()
    pump.writer._connection.execute(
        "INSERT INTO command_outbox VALUES('project--new', 'queued')")
    for _ in range(3):
        pump.poll_once()
    assert "project--new" in {project for project, _ in worker.calls}


@pytest.mark.parametrize("params", [
    {"projects_per_poll": 0}, {"projects_per_poll": 101},
    {"projects_per_poll": True}, {"commands_per_project": 0},
    {"commands_per_project": 101}, {"commands_per_project": 1.5},
    {"interval_seconds": True},
])
def test_limits_reject_invalid_values(params):
    pump, worker = make_pump(1)
    with pytest.raises(ValueError):
        CommandDeliveryPump(
            writer=pump.writer, workforce=worker,
            actor_for_project=lambda _: {}, github_for_project=lambda _: None,
            **params)


def test_builder_preserves_publication_bounds():
    pump, worker = make_pump(1)
    builder = CommandDeliveryPumpBuilder(
        actor_for_project=lambda x: {"project": x},
        github_for_project=lambda x: x,
        projects_per_poll=2, commands_per_project=3)
    built = builder.for_writer(pump.writer, None, worker)
    assert (built.projects_per_poll, built.commands_per_project) == (2, 3)
