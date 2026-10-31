"""Bounded durable GitHub command outbox delivery for active browser shifts.

Only the C-committed, shift-addressed command bytes are published. GitHub's
verified immutable readback determines publication. No ChatGPT browser is
started, reactivated or inferred active by this deterministic coordinator.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import Event, Thread

from studio.workforce import Workforce
from studio.storage.workforce import WorkforceStorage


class CommandDeliveryPump:
    def __init__(self, *, writer: WorkforceStorage,
                 workforce: Workforce, actor_for_project: Callable[[str], Mapping],
                 github_for_project: Callable[[str], object],
                 interval_seconds: float = 3.0,
                 projects_per_poll: int = 8,
                 commands_per_project: int = 1):
        if not isinstance(writer, WorkforceStorage) or not isinstance(workforce, Workforce):
            raise TypeError("real C/I workforce required for GitHub outbox delivery")
        if not callable(actor_for_project) or not callable(github_for_project):
            raise TypeError("trusted operator proof and GitHub binding required")
        if (isinstance(interval_seconds, bool) or
                not isinstance(interval_seconds, (int, float)) or
                not 0.25 <= interval_seconds <= 3600):
            raise ValueError("outbox polling interval outside authorized bound")
        if (type(projects_per_poll) is not int or
                not 1 <= projects_per_poll <= 100):
            raise ValueError("invalid outbox project scan bound")
        if (type(commands_per_project) is not int or
                not 1 <= commands_per_project <= 100):
            raise ValueError("invalid outbox command publication bound")
        self.writer = writer
        self.workforce = workforce
        self.actor_for_project = actor_for_project
        self.github_for_project = github_for_project
        self.interval_seconds = interval_seconds
        self.projects_per_poll = projects_per_poll
        self.commands_per_project = commands_per_project
        self._last_project = ""
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None
        self.last_error: Exception | None = None

    def _projects(self) -> list[str]:
        # Round-robin selection avoids permanent starvation when projects
        # exceed the per-cycle budget. The cursor is a scheduling hint only;
        # all authoritative pending rows live in C's durable outbox.
        query = ("SELECT DISTINCT project_id FROM command_outbox "
                 "WHERE state IN ('queued','unknown') AND project_id > ? "
                 "ORDER BY project_id LIMIT ?")
        with self.writer._lock:
            connection = self.writer._connection
            projects = [row[0] for row in connection.execute(
                query, (self._last_project, self.projects_per_poll))]
            if len(projects) < self.projects_per_poll and self._last_project:
                remaining = self.projects_per_poll - len(projects)
                projects.extend(row[0] for row in connection.execute(
                    "SELECT DISTINCT project_id FROM command_outbox "
                    "WHERE state IN ('queued','unknown') AND project_id <= ? "
                    "ORDER BY project_id LIMIT ?",
                    (self._last_project, remaining)))
        if projects:
            self._last_project = projects[-1]
        return projects

    def poll_once(self) -> dict[str, int]:
        totals = {"published": 0, "unknown": 0, "rejected": 0}
        self.last_error = None
        for project_id in self._projects():
            if self._stop.is_set():
                break
            try:
                actor = self.actor_for_project(project_id)
                provider = self.github_for_project(project_id)
                result = self.workforce.publish_pending(
                    actor, project_id, provider=provider,
                    limit=self.commands_per_project)
            except Exception as exc:
                # A failing project's identity/provider cannot starve other
                # projects. Its pending commands stay in the durable outbox.
                self.last_error = exc
                continue
            for key in totals:
                totals[key] += int(result[key])
        return totals

    def _loop(self):
        while not self._stop.is_set():
            # Preserve hints received during an in-flight provider call.
            self._wake.clear()
            try:
                self.poll_once()
            except Exception as exc:
                self.last_error = exc
            if not self._stop.is_set():
                self._wake.wait(self.interval_seconds)

    def request_reconciliation(self) -> None:
        """Wake the bounded readback loop without performing provider I/O."""
        self._wake.set()

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("GitHub outbox pump already started")
        self._stop.clear()
        self._wake.clear()
        self._thread = Thread(target=self._loop, name="studio-github-outbox", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.interval_seconds + 1.0))
            if self._thread.is_alive():
                raise RuntimeError("GitHub outbox publisher did not stop cleanly")
            self._thread = None


class CommandDeliveryPumpBuilder:
    def __init__(self, *, actor_for_project, github_for_project,
                 interval_seconds: float = 3.0,
                 projects_per_poll: int = 8,
                 commands_per_project: int = 1):
        self.actor_for_project = actor_for_project
        self.github_for_project = github_for_project
        self.interval_seconds = interval_seconds
        self.projects_per_poll = projects_per_poll
        self.commands_per_project = commands_per_project

    def for_writer(self, writer: WorkforceStorage, _root,
                   workforce: Workforce) -> CommandDeliveryPump:
        return CommandDeliveryPump(
            writer=writer, workforce=workforce,
            actor_for_project=self.actor_for_project,
            github_for_project=self.github_for_project,
            interval_seconds=self.interval_seconds,
            projects_per_poll=self.projects_per_poll,
            commands_per_project=self.commands_per_project)
