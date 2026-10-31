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
                 interval_seconds: float = 3.0):
        if not isinstance(writer,WorkforceStorage) or not isinstance(workforce,Workforce):
            raise TypeError("real C/I workforce required for GitHub outbox delivery")
        if not callable(actor_for_project) or not callable(github_for_project):
            raise TypeError("trusted operator proof and GitHub binding required")
        if not isinstance(interval_seconds,(int,float)) or not 0.25<=interval_seconds<=3600:
            raise ValueError("outbox polling interval outside authorized bound")
        self.writer=writer
        self.workforce=workforce
        self.actor_for_project=actor_for_project
        self.github_for_project=github_for_project
        self.interval_seconds=interval_seconds
        self._stop=Event()
        self._thread:Thread|None=None
        self.last_error:Exception|None=None

    def poll_once(self) -> dict[str,int]:
        with self.writer._lock:
            projects=[item[0] for item in self.writer._connection.execute(
                "SELECT DISTINCT project_id FROM command_outbox "
                "WHERE state IN ('queued','unknown') ORDER BY project_id LIMIT 101")]
        if len(projects)>100:
            raise RuntimeError("ready project count exceeds bounded publisher scan")
        totals={"published":0,"unknown":0,"rejected":0}
        for project_id in projects:
            actor=self.actor_for_project(project_id)
            provider=self.github_for_project(project_id)
            result=self.workforce.publish_pending(actor,project_id,provider=provider)
            for key in totals:
                totals[key]+=int(result[key])
        return totals

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
                self.last_error=None
            except Exception as exc:
                # A single unavailable provider must not invent success or
                # discard the C outbox. The next bounded cycle retries QUEUED
                # or strictly readbacks UNKNOWN without repeating its write.
                self.last_error=exc
            self._stop.wait(self.interval_seconds)

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("GitHub outbox pump already started")
        self._stop.clear()
        self._thread=Thread(target=self._loop,name="studio-github-outbox",daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0,self.interval_seconds+1.0))
            if self._thread.is_alive():
                raise RuntimeError("GitHub outbox publisher did not stop cleanly")
            self._thread=None


class CommandDeliveryPumpBuilder:
    def __init__(self, *, actor_for_project, github_for_project,
                 interval_seconds: float = 3.0):
        self.actor_for_project=actor_for_project
        self.github_for_project=github_for_project
        self.interval_seconds=interval_seconds

    def for_writer(self, writer: WorkforceStorage, _root,
                   workforce: Workforce) -> CommandDeliveryPump:
        return CommandDeliveryPump(
            writer=writer,workforce=workforce,
            actor_for_project=self.actor_for_project,
            github_for_project=self.github_for_project,
            interval_seconds=self.interval_seconds)
