"""Bounded C/G reconciliation of previously dispatched GitHub Actions runs.

A run with no verified remote observation stays UNKNOWN. This pump never
calls Actions.dispatch, never retries a workflow write and never starts AI.
"""
from __future__ import annotations

import hashlib
import json
from threading import Event, Thread
from typing import Callable

from studio.integrations.api_ports import GOperatorPorts
from studio.integrations.ci import BuildObservation
from studio.storage.builds import BuildAPSWRepository
from studio.storage.workforce import WorkforceStorage


class BuildRecoveryPump:
    def __init__(self, *, writer: WorkforceStorage,
                 provider_for_project: Callable[[str], GOperatorPorts],
                 interval_seconds: float = 30.0,
                 projects_per_poll: int = 8,
                 builds_per_project: int = 2):
        if not isinstance(writer, WorkforceStorage) or not callable(provider_for_project):
            raise TypeError('real C writer and trusted G project resolver required')
        if (type(interval_seconds) not in (int, float) or
                not 1 <= interval_seconds <= 3600 or
                type(projects_per_poll) is not int or not 1 <= projects_per_poll <= 100 or
                type(builds_per_project) is not int or not 1 <= builds_per_project <= 100):
            raise ValueError('invalid bounded build reconciliation schedule')
        self.writer = writer
        self.providers = provider_for_project
        self.repository = BuildAPSWRepository(writer)
        self.interval_seconds = interval_seconds
        self.projects_per_poll = projects_per_poll
        self.builds_per_project = builds_per_project
        self._last_project = ''
        self._last_builds: dict[str, str] = {}
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None
        self.last_error: Exception | None = None

    def _projects(self) -> list[str]:
        query = ("SELECT DISTINCT c.project_id FROM ci_runs c JOIN effect_intents e "
                 "ON e.project_id=c.project_id AND e.subject_id=c.ci_id "
                 "AND e.action='build.dispatch' "
                 "WHERE c.status IN ('queued','unknown','running') "
                 "AND e.state IN ('dispatched','unknown','observed') ")
        with self.writer._lock:
            db = self.writer._connection
            first = [row[0] for row in db.execute(
                query+"AND c.project_id>? ORDER BY c.project_id LIMIT ?",
                (self._last_project,self.projects_per_poll))]
            if len(first) < self.projects_per_poll and self._last_project:
                first.extend(row[0] for row in db.execute(
                    query+"AND c.project_id<=? ORDER BY c.project_id LIMIT ?",
                    (self._last_project,self.projects_per_poll-len(first))))
        if first:
            self._last_project = first[-1]
        return first

    def _pending(self, project_id: str) -> list[dict]:
        def read(tx):
            # Each project has its own deterministic round-robin cursor.
            # A perpetually UNKNOWN early build must not starve later runs.
            query = (
                "SELECT c.ci_id,c.source_revision_id,c.remote_run_id,s.commit_sha,e.effect_id,"
                "e.request_id FROM ci_runs c JOIN effect_intents e "
                "ON e.project_id=c.project_id AND e.subject_id=c.ci_id "
                "AND e.action='build.dispatch' JOIN source_revisions s "
                "ON s.project_id=c.project_id AND s.revision_id=c.source_revision_id "
                "WHERE c.project_id=? AND c.status IN ('queued','unknown','running') "
                "AND e.state IN ('dispatched','unknown','observed') "
            )
            cursor = self._last_builds.get(project_id, '')
            rows = tx.all(query + "AND c.ci_id>? ORDER BY c.ci_id LIMIT ?",
                          (project_id, cursor, self.builds_per_project))
            if len(rows) < self.builds_per_project and cursor:
                rows.extend(tx.all(query + "AND c.ci_id<=? ORDER BY c.ci_id LIMIT ?",
                                   (project_id, cursor, self.builds_per_project-len(rows))))
            if rows:
                self._last_builds[project_id] = rows[-1]['ci_id']
            result = []
            for row in rows:
                request = row['request_id']
                if not isinstance(request,str) or not request.startswith('G.build.'):
                    raise ValueError('C build effect has no source request identity')
                original = request[len('G.build.'):]
                receipt = tx.one(
                    "SELECT r.result_json,i.result_digest,i.status "
                    "FROM workforce_command_results r JOIN idempotency i "
                    "ON i.project_id=r.project_id AND i.action_owner=r.action_owner "
                    "AND i.request_id=r.request_id "
                    "WHERE r.project_id=? AND r.action_owner='WG-G' AND r.request_id=?",
                    (project_id,original))
                if receipt is None or receipt['status']!='finished' or (
                        hashlib.sha256(receipt['result_json'].encode('utf-8')).hexdigest()
                        != receipt['result_digest']):
                    raise ValueError('C build command result lacks verified idempotency bytes')
                try:
                    recorded = json.loads(receipt['result_json'])
                except (ValueError,TypeError) as exc:
                    raise ValueError('invalid C build result JSON') from exc
                if (not isinstance(recorded,dict) or
                        recorded.get('project_id')!=project_id or
                        recorded.get('ci_id')!=row['ci_id'] or
                        recorded.get('effect_id')!=row['effect_id'] or
                        recorded.get('source_sha')!=row['commit_sha'] or
                        not isinstance(recorded.get('target_id'),str) or
                        not isinstance(recorded.get('intent_id'),str)):
                    raise ValueError('C build result diverges from source/effect journal')
                recorded['_remote_run_id'] = row['remote_run_id']
                result.append(recorded)
            return result
        return self.writer.read(project_id,read)

    def poll_once(self) -> dict[str,int]:
        totals = {'observed':0,'unknown':0,'errors':0}
        self.last_error = None
        for project_id in self._projects():
            if self._stop.is_set():
                break
            try:
                provider = self.providers(project_id)
                if not isinstance(provider,GOperatorPorts) or provider.binding.project_id!=project_id:
                    raise TypeError('G build readback is not bound to authorized project')
                pending = self._pending(project_id)
            except Exception as exc:
                self.last_error = exc
                totals['errors'] += 1
                continue
            for record in pending:
                if self._stop.is_set():
                    break
                try:
                    known_run_id = record['_remote_run_id']
                    if known_run_id:
                        if (not isinstance(known_run_id,str) or not known_run_id.isdecimal()
                                or int(known_run_id) < 1):
                            raise ValueError('invalid C GitHub Actions run ID')
                        # Once a verified GitHub run ID is persisted, direct
                        # pinned run readback replaces costly list pagination.
                        run = provider.actions.verify_run(
                            project_id=project_id,target_id=record['target_id'],
                            intent_id=record['intent_id'],run_id=int(known_run_id),
                            expected_sha=record['source_sha'])
                    else:
                        run = provider.actions.observe(
                            project_id=project_id,target_id=record['target_id'],
                            source_sha=record['source_sha'],intent_id=record['intent_id'])
                    if run is not None and not isinstance(run,BuildObservation):
                        raise TypeError('G returned an unverified Actions run observation')
                    self.repository.observe(project_id=project_id,
                                            ci_id=record['ci_id'],run=run)
                    totals['observed' if run is not None else 'unknown'] += 1
                except Exception as exc:
                    # An uncertain provider result stays in C for later exact
                    # readback; it is never re-dispatched or fabricated.
                    self.last_error = exc
                    totals['errors'] += 1
        return totals

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                self.last_error = exc
            self._wake.wait(self.interval_seconds)
            self._wake.clear()

    def request_reconciliation(self) -> None:
        """Wake the bounded readback loop without performing provider I/O."""
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError('build reconciliation pump already running')
        self._stop.clear()
        self._wake.clear()
        self._thread = Thread(target=self._loop,name='studio-github-build-readback',daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0,self.interval_seconds+1.0))
            if self._thread.is_alive():
                raise RuntimeError('build readback was still active at shutdown')
            self._thread = None
