"""Project-scoped, C-transaction-backed feature production and task scheduler.

Seven public camelCase methods implement the frozen OpenAPI D operation signatures.
Additional narrow D-domain methods provide incremental ready evaluation,
verified-QA completion, expiry reconciliation and manual assignment metadata.
Only a human can actually initiate the ChatGPT browser conversation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .graph import GraphFault, TaskGraph
from .models import FeatureBrief, PreparedAssignment, TaskSpec
from .ports import (AdvisoryStopPort, AtomicResourceReservations, Authorization,
                    IndependentAcceptance, ScopedStorage, ScopedTx, VerifiedBriefs, VerifiedTaskProgress)

MAX_BRIEF_TASKS = 25000
MAX_PAGE = 256
MAX_BRIEF_BYTES = 4 * 1024 * 1024


class ProductionFault(Exception):
    """Code names match frozen CONTRACTS/ports.pyi Error enum."""
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _digest(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _request(operation: str, request: Mapping[str, Any], keys: set[str]) -> tuple[str, str]:
    if not isinstance(request, dict) or set(request) != keys:
        raise ProductionFault('invalid', f'{operation}: request fields differ from contract')
    request_id = request.get('request_id')
    if not isinstance(request_id, str) or not request_id or len(request_id) > 200:
        raise ProductionFault('invalid', 'request_id required')
    return request_id, _digest(operation, request)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ProductionFault('invalid', f'{field} must be a nonempty string')
    return value


def _int(value: object, field: str, *, positive: bool = False) -> int:
    if type(value) is not int or (positive and value <= 0):
        raise ProductionFault('invalid', f'{field} must be a valid integer')
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProductionFault('internal', 'clock must return aware UTC instant')
    return value.astimezone(timezone.utc)


class ProductionService:
    def __init__(self, *, storage: ScopedStorage, authorization: Authorization,
                 briefs: VerifiedBriefs, reservations: AtomicResourceReservations | None = None,
                 acceptance: IndependentAcceptance | None = None,
                 progress: VerifiedTaskProgress | None = None,
                 stop: AdvisoryStopPort | None = None,
                 clock: Callable[[], datetime] | None = None,
                 lease_seconds: int = 3600):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
            raise ValueError('lease_seconds must be 1..86400')
        self.storage = storage
        self.authorization = authorization
        self.briefs = briefs
        self.reservations = reservations
        self.acceptance = acceptance
        self.progress = progress
        self.stop = stop
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_seconds = lease_seconds

    def _authorize(self, actor: Mapping[str, Any], project: str, action: str,
                   subject: str = '') -> None:
        _text(project, 'project_id')
        if not isinstance(actor, dict) or not isinstance(actor.get('principal_id'), str) or not actor['principal_id']:
            raise ProductionFault('forbidden', 'authenticated principal required')
        if project not in actor.get('allowed_project_ids', ()) or not actor.get('trusted_proof_ref'):
            raise ProductionFault('forbidden', 'project scope/verified identity required')
        # This is a *required*, external J/A trusted check, not a hard-coded policy default.
        self.authorization.require(actor, project, action, subject)

    def _brief(self, project_id: str, digest: str) -> FeatureBrief:
        _text(digest, 'brief_digest')
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ProductionFault('invalid', 'brief_digest must be lowercase sha256')
        raw = self.briefs.get_verified(project_id=project_id, content_sha256=digest)
        if not isinstance(raw, bytes) or len(raw) > MAX_BRIEF_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise ProductionFault('conflict', 'unavailable or mismatched verified brief bytes')
        try:
            return FeatureBrief.parse(json.loads(raw))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise ProductionFault('invalid', f'invalid owner-supplied brief: {exc}') from exc

    def _canonical(self, tx: ScopedTx, project: str, snapshot: str) -> None:
        row = tx.one('SELECT snapshot_id FROM canon_snapshots WHERE project_id=? AND snapshot_id=?', (project, snapshot))
        if row is None:
            raise ProductionFault('stale_snapshot', 'Canon snapshot does not exist in project')
        # Current edition pins can be checked without mutating governance tables.
        row = tx.one('''SELECT 1 AS valid FROM canon_snapshots s
            JOIN active_bible_editions d ON d.project_id=s.project_id AND d.bible_id=s.design_bible_id
                AND d.edition_id=s.design_edition_id AND d.kind='design'
            JOIN active_bible_editions t ON t.project_id=s.project_id AND t.bible_id=s.technical_bible_id
                AND t.edition_id=s.technical_edition_id AND t.kind='technical'
            WHERE s.project_id=? AND s.snapshot_id=?''', (project, snapshot))
        if row is None:
            raise ProductionFault('stale_snapshot', 'pinned Canon snapshot no longer current')

    def _source(self, tx: ScopedTx, project: str, revision: str) -> None:
        if tx.one('SELECT revision_id FROM source_revisions WHERE project_id=? AND revision_id=? AND state=?',
                  (project, revision, 'canonical')) is None:
            raise ProductionFault('conflict', 'pinned source revision is not canonical')

    def _feature(self, tx: ScopedTx, project: str, feature_id: str) -> Mapping[str, Any]:
        row = tx.one('''SELECT project_id,feature_id,brief_digest,snapshot_id,source_revision_id,revision,state
                        FROM features WHERE project_id=? AND feature_id=?''', (project, feature_id))
        if row is None:
            raise ProductionFault('missing', 'feature not found in project')
        return row

    def _task(self, tx: ScopedTx, project: str, task_id: str) -> Mapping[str, Any]:
        row = tx.one('''SELECT project_id,task_id,feature_id,cabal_id,snapshot_id,role_id,
                            acceptance_digest,priority,revision,state
                        FROM tasks WHERE project_id=? AND task_id=?''', (project, task_id))
        if row is None:
            raise ProductionFault('missing', 'task not found in project')
        return row

    def _spec(self, tx: ScopedTx, project: str, task: Mapping[str, Any]) -> tuple[Mapping[str, Any], TaskSpec]:
        feature = self._feature(tx, project, str(task['feature_id']))
        brief = self._brief(project, str(feature['brief_digest']))
        for spec in brief.tasks:
            if _digest('task', project, feature['feature_id'], spec.key, spec.acceptance_digest) == task['task_id']:
                if (task['acceptance_digest'] != spec.acceptance_digest or
                    task['snapshot_id'] != feature['snapshot_id'] or
                    feature['source_revision_id'] != brief.source_revision_id or
                    task['role_id'] != spec.role_id):
                    raise ProductionFault('conflict', 'task pin disagrees with source feature brief')
                return feature, spec
        raise ProductionFault('conflict', 'task not backed by verified brief')

    def submitFeature(self, actor: Mapping[str, Any], project_id: str,
                      request: dict[str, Any]) -> dict[str, Any]:
        self._authorize(actor, project_id, 'submitFeature')
        rid, body_hash = _request('submitFeature', request,
                                  {'request_id', 'brief_digest', 'snapshot_id', 'acceptance_digest'})
        snapshot = _text(request['snapshot_id'], 'snapshot_id')
        acceptance = _text(request['acceptance_digest'], 'acceptance_digest')
        feature_id = _digest('feature', project_id, request['brief_digest'], snapshot)

        def commit(tx: ScopedTx) -> dict[str, Any]:
            brief = self._brief(project_id, request['brief_digest'])
            if brief.acceptance_digest != acceptance:
                raise ProductionFault('conflict', 'acceptance digest differs from verified owner brief')
            self._canonical(tx, project_id, snapshot)
            self._source(tx, project_id, brief.source_revision_id)
            if brief.repair_of_task_id:
                if self.acceptance is None:
                    raise ProductionFault('capability_unavailable', 'E independent QA port not configured')
                original = self._task(tx, project_id, brief.repair_of_task_id)
                if original['state'] != 'failed':
                    raise ProductionFault('conflict', 'repair requires a failed parent task')
                self.acceptance.require_failed(tx, project_id=project_id,
                    task_id=brief.repair_of_task_id, evidence_digest=brief.failure_evidence_digest)
            old = tx.one('SELECT feature_id FROM features WHERE project_id=? AND feature_id=?', (project_id, feature_id))
            if old:
                raise ProductionFault('conflict', 'feature with same immutable brief and snapshot already exists')
            tx.change('''INSERT INTO features(project_id,feature_id,brief_digest,snapshot_id,
                       source_revision_id,revision,state) VALUES (?,?,?,?,?,1,'proposed')''',
                      (project_id, feature_id, request['brief_digest'], snapshot, brief.source_revision_id))
            return {'feature_id': feature_id, 'state': 'proposed'}

        return self.storage.command(project_id, 'D.submitFeature', rid, body_hash, commit)

    def planFeature(self, actor: Mapping[str, Any], project_id: str, feature_id: str,
                    request: dict[str, Any]) -> dict[str, Any]:
        self._authorize(actor, project_id, 'planFeature', feature_id)
        rid, body_hash = _request('planFeature', request, {'request_id', 'snapshot_id', 'expected_revision'})
        snapshot = _text(request['snapshot_id'], 'snapshot_id')
        expected = _int(request['expected_revision'], 'expected_revision', positive=True)

        def commit(tx: ScopedTx) -> dict[str, Any]:
            feature = self._feature(tx, project_id, feature_id)
            if feature['revision'] != expected:
                raise ProductionFault('stale_revision', 'feature expected_revision mismatch')
            if feature['state'] != 'proposed':
                raise ProductionFault('conflict', 'feature not in proposed state')
            if feature['snapshot_id'] != snapshot:
                raise ProductionFault('stale_snapshot', 'feature snapshot differs from request')
            self._canonical(tx, project_id, snapshot)
            self._source(tx, project_id, str(feature['source_revision_id']))
            brief = self._brief(project_id, str(feature['brief_digest']))
            if brief.source_revision_id != feature['source_revision_id']:
                raise ProductionFault('conflict', 'brief source revision mismatch')
            if len(brief.tasks) > MAX_BRIEF_TASKS:
                raise ProductionFault('invalid', 'single feature graph exceeds bounded materialization limit')
            try:
                graph = TaskGraph.from_specs(brief.tasks)
            except GraphFault as exc:
                raise ProductionFault('invalid', str(exc)) from exc
            specs = {spec.key: spec for spec in brief.tasks}
            task_ids = {key: _digest('task', project_id, feature_id, key, specs[key].acceptance_digest)
                        for key in graph.order()}
            for key in graph.order():
                spec = specs[key]
                if tx.one('SELECT 1 FROM cabals WHERE project_id=? AND cabal_id=? AND state=?',
                          (project_id, spec.cabal_id, 'active')) is None:
                    raise ProductionFault('conflict', 'requested cabal is not active in project')
                if tx.one('SELECT 1 FROM specialist_roles WHERE role_id=? AND state=?',
                          (spec.role_id, 'ready')) is None:
                    raise ProductionFault('conflict', 'requested specialist role unavailable')
                if not tx.one('SELECT 1 FROM cabal_members WHERE project_id=? AND cabal_id=? AND role_id=? LIMIT 1',
                              (project_id, spec.cabal_id, spec.role_id)):
                    raise ProductionFault('conflict', 'cabal lacks authorized specialist staffing')
                self._package_grant(tx, project_id, spec)
                tx.change('''INSERT INTO tasks(project_id,task_id,feature_id,cabal_id,snapshot_id,
                           role_id,acceptance_digest,priority,revision,state)
                           VALUES (?,?,?,?,?,?,?,?,1,?)''',
                          (project_id, task_ids[key], feature_id, spec.cabal_id, snapshot,
                           spec.role_id, spec.acceptance_digest, spec.priority,
                           'pending' if spec.depends_on else 'ready'))
            for key, spec in specs.items():
                for dep in spec.depends_on:
                    tx.change('INSERT INTO task_dependencies(project_id,task_id,depends_on_task_id) VALUES (?,?,?)',
                              (project_id, task_ids[key], task_ids[dep]))
            if tx.change('UPDATE features SET revision=revision+1,state=? WHERE project_id=? AND feature_id=? AND revision=? AND state=?',
                         ('planned', project_id, feature_id, expected, 'proposed')) != 1:
                raise ProductionFault('stale_revision', 'feature changed during planning')
            return {'feature_id': feature_id, 'task_ids': [task_ids[key] for key in graph.order()]}

        return self.storage.command(project_id, 'D.planFeature', rid, body_hash, commit)

    def _package_grant(self, tx: ScopedTx, project_id: str, spec: TaskSpec) -> None:
        if not (spec.package_digest and spec.entrypoint_id and spec.grant_id):
            raise ProductionFault('invalid', 'package, entrypoint and grant pins required together')
        row = tx.one('''SELECT 1 FROM project_packages p JOIN package_revisions r
            ON r.package_digest=p.package_digest JOIN tool_grants g
            ON g.project_id=p.project_id AND g.package_digest=p.package_digest
            JOIN tool_entrypoints e ON e.package_digest=g.package_digest AND e.entrypoint_id=g.entrypoint_id
            WHERE p.project_id=? AND p.package_digest=? AND p.enabled=1 AND r.state='installed'
             AND g.grant_id=? AND g.entrypoint_id=? AND g.revoked=0''',
                     (project_id, spec.package_digest, spec.grant_id, spec.entrypoint_id))
        if row is None:
            raise ProductionFault('capability_unavailable', 'tool package/grant pin disabled or revoked')

    def listFeatures(self, actor: Mapping[str, Any], project_id: str) -> dict[str, Any]:
        self._authorize(actor, project_id, 'listFeatures')
        def read(tx: ScopedTx) -> dict[str, Any]:
            features: list[dict[str, Any]] = []
            cursor = ''
            while True:
                rows = tx.all('''SELECT feature_id,snapshot_id,state FROM features
                              WHERE project_id=? AND feature_id>? ORDER BY feature_id LIMIT ?''',
                              (project_id, cursor, MAX_PAGE))
                features.extend(dict(r) for r in rows)
                if len(rows) < MAX_PAGE:
                    break
                cursor = str(rows[-1]['feature_id'])
            return {'features': features}
        return self.storage.read(project_id, read)

    def listTasks(self, actor: Mapping[str, Any], project_id: str) -> dict[str, Any]:
        self._authorize(actor, project_id, 'listTasks')
        def read(tx: ScopedTx) -> dict[str, Any]:
            tasks: list[dict[str, Any]] = []
            cursor = ''
            while True:
                # Task PK(project_id, task_id) supports bounded ordered keyset reads
                # without constructing a potentially unbounded temp priority sort.
                rows = tx.all('''SELECT task_id,feature_id,state,priority,revision FROM tasks
                        WHERE project_id=? AND task_id>? ORDER BY task_id LIMIT ?''',
                        (project_id, cursor, MAX_PAGE))
                tasks.extend(dict(r) for r in rows)
                if len(rows) < MAX_PAGE:
                    break
                cursor = str(rows[-1]['task_id'])
            return {'tasks': tasks}
        return self.storage.read(project_id, read)

    def assignTask(self, actor: Mapping[str, Any], project_id: str, task_id: str,
                   request: dict[str, Any]) -> dict[str, Any]:
        self._authorize(actor, project_id, 'assignTask', task_id)
        rid, body_hash = _request('assignTask', request, {'request_id', 'cabal_id', 'expected_revision'})
        cabal_id = _text(request['cabal_id'], 'cabal_id')
        expected = _int(request['expected_revision'], 'expected_revision', positive=True)

        def commit(tx: ScopedTx) -> dict[str, Any]:
            task = self._task(tx, project_id, task_id)
            if task['revision'] != expected:
                raise ProductionFault('stale_revision', 'task revision changed')
            if task['state'] not in ('pending', 'ready', 'blocked', 'failed'):
                raise ProductionFault('conflict', 'task already being worked or completed')
            if tx.one('SELECT 1 FROM leases WHERE project_id=? AND task_id=? AND status=? LIMIT 1',
                      (project_id, task_id, 'active')):
                raise ProductionFault('conflict', 'task has active lease')
            if tx.one('SELECT 1 FROM cabals WHERE project_id=? AND cabal_id=? AND state=?',
                      (project_id, cabal_id, 'active')) is None:
                raise ProductionFault('missing', 'target cabal is not active in project')
            if not tx.one('SELECT 1 FROM cabal_members WHERE project_id=? AND cabal_id=? AND role_id=?',
                          (project_id, cabal_id, task['role_id'])):
                raise ProductionFault('conflict', 'cabal not staffed for task role')
            if tx.change('UPDATE tasks SET cabal_id=?,revision=revision+1 WHERE project_id=? AND task_id=? AND revision=?',
                         (cabal_id, project_id, task_id, expected)) != 1:
                raise ProductionFault('stale_revision', 'concurrent cabal change')
            return {'task_id': task_id, 'cabal_id': cabal_id, 'revision': expected + 1}

        return self.storage.command(project_id, 'D.assignTask', rid, body_hash, commit)

    def dispatchTask(self, actor: Mapping[str, Any], project_id: str, task_id: str,
                     request: dict[str, Any]) -> dict[str, Any]:
        self._authorize(actor, project_id, 'dispatchTask', task_id)
        rid, body_hash = _request('dispatchTask', request, {'request_id', 'expected_revision', 'worker_id'})
        expected = _int(request['expected_revision'], 'expected_revision', positive=True)
        worker = _text(request['worker_id'], 'worker_id')

        def commit(tx: ScopedTx) -> dict[str, Any]:
            task = self._task(tx, project_id, task_id)
            if task['revision'] != expected:
                raise ProductionFault('stale_revision', 'task revision changed')
            if task['state'] != 'ready':
                raise ProductionFault('conflict', 'only ready tasks may be leased')
            if tx.one('''SELECT 1 FROM task_dependencies d JOIN tasks t ON t.project_id=d.project_id
                  AND t.task_id=d.depends_on_task_id WHERE d.project_id=? AND d.task_id=?
                  AND t.state<>'accepted' LIMIT 1''', (project_id, task_id)):
                raise ProductionFault('conflict', 'dependencies are not independently accepted')
            feature, spec = self._spec(tx, project_id, task)
            self._canonical(tx, project_id, str(task['snapshot_id']))
            self._source(tx, project_id, str(feature['source_revision_id']))
            self._package_grant(tx, project_id, spec)
            if not tx.one('SELECT 1 FROM cabal_members WHERE project_id=? AND cabal_id=? AND member_id=? AND role_id=?',
                          (project_id, task['cabal_id'], worker, task['role_id'])):
                raise ProductionFault('forbidden', 'worker not assigned to cabal and role')
            if tx.one('SELECT 1 FROM cabals WHERE project_id=? AND cabal_id=? AND state=?',
                      (project_id, task['cabal_id'], 'active')) is None:
                raise ProductionFault('conflict', 'cabal is not active')
            now = _utc(self.clock())
            now_str = now.isoformat()
            # A departed browser is not presumed alive: leases have explicit expiry fences.
            if tx.one('''SELECT 1 FROM leases WHERE project_id=? AND task_id=?
                         AND status='active' AND expires_at>? LIMIT 1''', (project_id, task_id, now_str)):
                raise ProductionFault('conflict', 'task already actively leased')
            if tx.one('''SELECT 1 FROM leases WHERE project_id=? AND worker_id=?
                         AND status='active' AND expires_at>? LIMIT 1''', (project_id, worker, now_str)):
                raise ProductionFault('conflict', 'worker already has active lease')
            capacity = tx.one('SELECT COUNT(*) AS n FROM cabal_members WHERE project_id=? AND cabal_id=? AND role_id=?',
                              (project_id, task['cabal_id'], task['role_id']))['n']
            active = tx.one('''SELECT COUNT(*) AS n FROM leases l JOIN tasks t
                  ON t.project_id=l.project_id AND t.task_id=l.task_id
                  WHERE l.project_id=? AND t.cabal_id=? AND t.role_id=?
                  AND l.status='active' AND l.expires_at>?''',
                            (project_id, task['cabal_id'], task['role_id'], now_str))['n']
            if active >= capacity:
                raise ProductionFault('conflict', 'cabal role at live lease capacity')
            gen = tx.one('SELECT COALESCE(MAX(generation),0)+1 AS generation FROM leases WHERE project_id=? AND task_id=?',
                         (project_id, task_id))['generation']
            operation = _digest('lease', project_id, task_id, gen)
            if spec.resources:
                if self.reservations is None:
                    raise ProductionFault('capability_unavailable', 'F reservation port not configured')
                self.reservations.reserve(tx, project_id=project_id, operation_id=operation,
                                          resource_amounts=spec.resources)
            # Expired rows are historically retained; generation always increases.
            tx.change('''UPDATE leases SET status='expired' WHERE project_id=? AND task_id=?
                         AND status='active' AND expires_at<=?''', (project_id, task_id, now_str))
            tx.change('''INSERT INTO leases(project_id,task_id,generation,worker_id,expires_at,status)
                         VALUES (?,?,?,?,?,'active')''',
                      (project_id, task_id, gen, worker,
                       (now + timedelta(seconds=self.lease_seconds)).isoformat()))
            if tx.change('UPDATE tasks SET state=?,revision=revision+1 WHERE project_id=? AND task_id=? AND revision=? AND state=?',
                         ('leased', project_id, task_id, expected, 'ready')) != 1:
                raise ProductionFault('stale_revision', 'concurrent dispatch')
            # Preserve frozen feature state machine, with no inferred acceptance.
            if feature['state'] in ('planned','blocked'):
                tx.change("UPDATE features SET state='running',revision=revision+1 WHERE project_id=? AND feature_id=? AND state IN ('planned','blocked')",
                          (project_id, task['feature_id']))
            return {'task_id': task_id, 'lease_generation': gen}

        return self.storage.command(project_id, 'D.dispatchTask', rid, body_hash, commit)

    def cancelTask(self, actor: Mapping[str, Any], project_id: str, task_id: str,
                   request: dict[str, Any]) -> dict[str, Any]:
        self._authorize(actor, project_id, 'cancelTask', task_id)
        rid, body_hash = _request('cancelTask', request, {'request_id', 'expected_revision', 'reason'})
        expected = _int(request['expected_revision'], 'expected_revision', positive=True)
        reason = _text(request['reason'], 'reason')
        affected: list[int] = []

        def commit(tx: ScopedTx) -> dict[str, Any]:
            task = self._task(tx, project_id, task_id)
            if task['revision'] != expected:
                raise ProductionFault('stale_revision', 'task revision changed')
            if task['state'] not in ('pending','ready','leased','running','blocked','failed'):
                raise ProductionFault('conflict', 'task cannot transition to canceled')
            leases = tx.all('SELECT generation FROM leases WHERE project_id=? AND task_id=? AND status=?',
                            (project_id, task_id, 'active'))
            affected.extend(r['generation'] for r in leases)
            if affected:
                _, spec = self._spec(tx, project_id, task)
                if spec.resources and self.reservations is None:
                    raise ProductionFault('capability_unavailable', 'F reservation release port unavailable')
            tx.change('UPDATE leases SET status=? WHERE project_id=? AND task_id=? AND status=?',
                      ('released', project_id, task_id, 'active'))
            if self.reservations is not None:
                for generation in affected:
                    self.reservations.release(tx, project_id=project_id,
                                              operation_id=_digest('lease', project_id, task_id, generation))
            if tx.change('UPDATE tasks SET state=?,revision=revision+1 WHERE project_id=? AND task_id=? AND revision=?',
                         ('canceled', project_id, task_id, expected)) != 1:
                raise ProductionFault('stale_revision', 'task canceled concurrently')
            return {'task_id': task_id, 'state': 'canceled'}

        result = self.storage.command(project_id, 'D.cancelTask', rid, body_hash, commit)
        # No claim that the browser stopped. I owns GitHub STOP; if notification
        # fails, I can reconcile canceled tasks against enrolled sessions.
        if self.stop is not None:
            for generation in affected:
                try:
                    self.stop.notify_task_canceled(project_id=project_id, task_id=task_id,
                                                   lease_generation=generation, reason=reason)
                except Exception:
                    pass  # cancellation persisted; I's independent stop reconciliation remains required
        return result

    def prepared_assignment(self, actor: Mapping[str, Any], project_id: str,
                            task_id: str, generation: int) -> PreparedAssignment:
        """For I: pinned facts only, never a browser invocation or GitHub write."""
        self._authorize(actor, project_id, 'dispatchTask', task_id)
        _int(generation, 'lease_generation', positive=True)
        def read(tx: ScopedTx) -> PreparedAssignment:
            task = self._task(tx, project_id, task_id)
            if task['state'] not in ('leased', 'running'):
                raise ProductionFault('conflict', 'task not leased')
            lease = tx.one('''SELECT worker_id,expires_at FROM leases WHERE project_id=? AND task_id=?
                            AND generation=? AND status='active' ''', (project_id, task_id, generation))
            if lease is None or lease['expires_at'] <= _utc(self.clock()).isoformat():
                raise ProductionFault('conflict', 'stale or expired assignment generation')
            feature, spec = self._spec(tx, project_id, task)
            self._canonical(tx, project_id, str(task['snapshot_id']))
            self._source(tx, project_id, str(feature['source_revision_id']))
            self._package_grant(tx, project_id, spec)
            return PreparedAssignment(project_id, task_id, task['feature_id'], generation,
                                      lease['worker_id'], task['snapshot_id'],
                                      feature['source_revision_id'], spec.package_digest,
                                      spec.entrypoint_id, spec.grant_id, task['acceptance_digest'], lease['expires_at'])
        return self.storage.read(project_id, read)

    def mark_accepted(self, actor: Mapping[str, Any], project_id: str, task_id: str,
                      *, generation: int, evidence_digest: str, expected_revision: int,
                      request_id: str) -> dict[str, Any]:
        """Internal E callback; requires independent QA verifier. Never trust chat STATUS."""
        self._authorize(actor, project_id, 'mark_accepted', task_id)
        if self.acceptance is None:
            raise ProductionFault('capability_unavailable', 'E acceptance verification port missing')
        _text(evidence_digest, 'evidence_digest')
        _text(request_id, 'request_id')
        _int(generation, 'generation', positive=True)
        _int(expected_revision, 'expected_revision', positive=True)
        body = _digest(task_id, generation, evidence_digest, expected_revision)
        def commit(tx: ScopedTx) -> dict[str, Any]:
            task = self._task(tx, project_id, task_id)
            if task['revision'] != expected_revision:
                raise ProductionFault('stale_revision', 'accepted task revision mismatch')
            if task['state'] != 'review':
                raise ProductionFault('conflict', 'only reviewed tasks may become accepted')
            self.acceptance.require_accepted(tx, project_id=project_id, task_id=task_id,
                                             lease_generation=generation, evidence_digest=evidence_digest)
            if tx.change("UPDATE tasks SET state='accepted',revision=revision+1 WHERE project_id=? AND task_id=? AND revision=? AND state='review'",
                         (project_id, task_id, expected_revision)) != 1:
                raise ProductionFault('stale_revision', 'task changed during QA acceptance')
            active = tx.all('SELECT generation FROM leases WHERE project_id=? AND task_id=? AND status=?',
                            (project_id, task_id, 'active'))
            if active:
                _, spec = self._spec(tx, project_id, task)
                if spec.resources and self.reservations is None:
                    raise ProductionFault('capability_unavailable', 'F reservation release port unavailable')
            tx.change('UPDATE leases SET status=? WHERE project_id=? AND task_id=? AND status=?',
                      ('released', project_id, task_id, 'active'))
            if self.reservations:
                for lease in active:
                    self.reservations.release(tx, project_id=project_id,
                                              operation_id=_digest('lease', project_id, task_id, lease['generation']))
            remaining = tx.one('''SELECT 1 FROM tasks WHERE project_id=? AND feature_id=? AND state<>'accepted' LIMIT 1''',
                               (project_id, task['feature_id']))
            if remaining is None:
                tx.change("UPDATE features SET state='review',revision=revision+1 WHERE project_id=? AND feature_id=? AND state='running'",
                          (project_id, task['feature_id']))
                tx.change("UPDATE features SET state='completed',revision=revision+1 WHERE project_id=? AND feature_id=? AND state='review'",
                          (project_id, task['feature_id']))
            awakened = self._refresh_ready(tx, project_id, MAX_PAGE)
            return {'task_id': task_id, 'state': 'accepted', 'ready_task_ids': awakened}
        return self.storage.command(project_id, 'D.mark_accepted', request_id, body, commit)

    def _refresh_ready(self, tx: ScopedTx, project_id: str, limit: int) -> list[str]:
        rows = tx.all('''SELECT t.task_id FROM tasks t
          WHERE t.project_id=? AND t.state='pending'
          AND NOT EXISTS(SELECT 1 FROM task_dependencies d
              JOIN tasks prereq ON prereq.project_id=d.project_id AND prereq.task_id=d.depends_on_task_id
              WHERE d.project_id=t.project_id AND d.task_id=t.task_id AND prereq.state<>'accepted')
          ORDER BY t.priority DESC, t.task_id LIMIT ?''', (project_id, limit))
        changed = []
        for row in rows:
            if tx.change("UPDATE tasks SET state='ready',revision=revision+1 WHERE project_id=? AND task_id=? AND state='pending'",
                         (project_id, row['task_id'])) == 1:
                changed.append(row['task_id'])
        return changed

    def refresh_ready(self, actor: Mapping[str, Any], project_id: str, *, limit: int = 128,
                      request_id: str) -> list[str]:
        self._authorize(actor, project_id, 'refresh_ready')
        _int(limit, 'limit', positive=True)
        if limit > MAX_PAGE:
            raise ProductionFault('invalid', 'readiness batch limit exceeded')
        _text(request_id, 'request_id')
        return self.storage.command(project_id, 'D.refresh_ready', request_id, _digest(limit),
                                    lambda tx: self._refresh_ready(tx, project_id, limit))

    def ready_candidates(self, actor: Mapping[str, Any], project_id: str, *, limit: int = 128) -> list[dict[str, Any]]:
        self._authorize(actor, project_id, 'listTasks')
        _int(limit, 'limit', positive=True)
        if limit > MAX_PAGE:
            raise ProductionFault('invalid', 'candidate batch limit exceeded')
        return self.storage.read(project_id, lambda tx: [dict(r) for r in tx.all('''
          SELECT t.task_id,t.priority,t.cabal_id,t.role_id,t.revision FROM tasks t
          WHERE t.project_id=? AND t.state='ready'
          AND NOT EXISTS (SELECT 1 FROM task_dependencies d JOIN tasks p
              ON p.project_id=d.project_id AND p.task_id=d.depends_on_task_id
              WHERE d.project_id=t.project_id AND d.task_id=t.task_id AND p.state<>'accepted')
          ORDER BY t.priority DESC,t.task_id LIMIT ?''', (project_id, limit))])

    def record_verified_progress(self, actor: Mapping[str, Any], project_id: str,
                                 task_id: str, *, generation: int, next_state: str,
                                 evidence_digest: str, expected_revision: int,
                                 request_id: str) -> dict[str, Any]:
        """Transition only on authenticated I/G/E proof, not on a browser's claim.

        A verified I HELLO may attest leased->running; G/I/E verify later
        submission and review receipts. E independently attests QA failures.
        This method cannot produce accepted state (mark_accepted is stricter).
        """
        self._authorize(actor, project_id, 'record_verified_progress', task_id)
        if self.progress is None:
            raise ProductionFault('capability_unavailable', 'verified I/G/E progress port missing')
        _int(generation, 'lease generation', positive=True)
        _int(expected_revision, 'expected_revision', positive=True)
        _text(evidence_digest, 'evidence_digest')
        _text(request_id, 'request_id')
        legal = {
            'leased': frozenset(('running','ready','blocked')),
            'running': frozenset(('submitted','failed','blocked')),
            'submitted': frozenset(('review','failed')),
            'review': frozenset(('failed','blocked')),
        }
        if next_state not in frozenset().union(*legal.values()):
            raise ProductionFault('invalid', 'not a permitted progress state')
        body = _digest(task_id, generation, next_state, evidence_digest, expected_revision)

        def commit(tx: ScopedTx) -> dict[str, Any]:
            task = self._task(tx, project_id, task_id)
            if task['revision'] != expected_revision:
                raise ProductionFault('stale_revision', 'task revision changed')
            if next_state not in legal.get(task['state'], ()):
                raise ProductionFault('conflict', 'invalid task transition')
            if not tx.one('''SELECT 1 FROM leases WHERE project_id=? AND task_id=? AND generation=?
                        AND status='active' AND expires_at>?''',
                          (project_id, task_id, generation, _utc(self.clock()).isoformat())):
                raise ProductionFault('conflict', 'unrecognized lease generation')
            self.progress.require_stage(tx, project_id=project_id, task_id=task_id,
                                        lease_generation=generation,
                                        next_state=next_state, evidence_digest=evidence_digest)
            if tx.change('UPDATE tasks SET state=?,revision=revision+1 WHERE project_id=? AND task_id=? AND revision=? AND state=?',
                         (next_state, project_id, task_id, expected_revision, task['state'])) != 1:
                raise ProductionFault('stale_revision', 'concurrent state transition')
            return {'task_id': task_id, 'state': next_state, 'revision': expected_revision + 1}

        return self.storage.command(project_id, 'D.record_verified_progress', request_id, body, commit)

    def reconcile_expired(self, actor: Mapping[str, Any], project_id: str, *, limit: int,
                          request_id: str) -> list[dict[str, Any]]:
        """Generation-fence timed-out leases; browser presence remains UNKNOWN.

        Leased work without verified progress returns to ready; running tasks
        enter blocked for human inspection. Neither counts as a browser STOP.
        """
        self._authorize(actor, project_id, 'reconcile_expired')
        _text(request_id, 'request_id')
        _int(limit, 'limit', positive=True)
        if limit > MAX_PAGE:
            raise ProductionFault('invalid', 'lease reconciliation batch too large')
        now = _utc(self.clock()).isoformat()
        def commit(tx: ScopedTx) -> list[dict[str, Any]]:
            rows = tx.all('''SELECT l.task_id,l.generation,t.state
                         FROM leases l JOIN tasks t ON t.project_id=l.project_id AND t.task_id=l.task_id
                         WHERE l.project_id=? AND l.status='active' AND l.expires_at<=?
                         ORDER BY l.expires_at,l.task_id LIMIT ?''', (project_id, now, limit))
            changed: list[dict[str, Any]] = []
            for row in rows:
                task = self._task(tx, project_id, row['task_id'])
                _, spec = self._spec(tx, project_id, task)
                if spec.resources and self.reservations is None:
                    raise ProductionFault('capability_unavailable', 'F reservation release port unavailable')
                if tx.change('''UPDATE leases SET status='expired' WHERE project_id=? AND task_id=?
                    AND generation=? AND status='active' AND expires_at<=?''',
                    (project_id, row['task_id'], row['generation'], now)) != 1:
                    continue
                if self.reservations:
                    self.reservations.release(tx, project_id=project_id,
                                              operation_id=_digest('lease', project_id, row['task_id'], row['generation']))
                new_state = {'leased':'ready', 'running':'blocked'}.get(row['state'])
                if new_state is not None:
                    tx.change('UPDATE tasks SET state=?,revision=revision+1 WHERE project_id=? AND task_id=? AND state=?',
                              (new_state, project_id, row['task_id'], row['state']))
                changed.append({'task_id': row['task_id'], 'generation': row['generation'],
                                'new_state': new_state or row['state']})
            return changed
        return self.storage.command(project_id, 'D.reconcile_expired', request_id,
                                    _digest(limit), commit)


    def pending_stop_requests(self, actor: Mapping[str, Any], project_id: str,
                              *, limit: int = 128) -> list[dict[str, Any]]:
        """I's recovery scan for canceled tasks with enrolled sessions still un-stopped.

        This read cannot claim GitHub publication or actual browser termination.
        I must publish its own STOP through G and record a verified GitHub receipt.
        """
        self._authorize(actor, project_id, 'cancelTask')
        _int(limit, 'limit', positive=True)
        if limit > MAX_PAGE:
            raise ProductionFault('invalid', 'stop reconciliation batch too large')
        return self.storage.read(project_id, lambda tx: [dict(r) for r in tx.all('''
            SELECT s.session_id,s.task_id,s.lease_generation
            FROM sessions s JOIN tasks t ON t.project_id=s.project_id AND t.task_id=s.task_id
            WHERE s.project_id=? AND t.state='canceled'
              AND s.state NOT IN ('stop_requested','completed_verified','failed_verified')
            ORDER BY s.session_id LIMIT ?''', (project_id, limit))])
