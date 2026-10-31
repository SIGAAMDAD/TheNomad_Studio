"""Value contracts derived only from verified, owner-supplied feature briefs.

A feature brief is an immutable content-addressed blob. This module does not create
creative requirements: every task and dependency must be present in that blob.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TaskSpec:
    key: str
    cabal_id: str
    role_id: str
    acceptance_digest: str
    depends_on: tuple[str, ...]
    priority: int = 0
    duration_units: int = 1
    resources: tuple[tuple[str, int], ...] = ()
    package_digest: str = ''
    entrypoint_id: str = ''
    grant_id: str = ''

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> 'TaskSpec':
        if not isinstance(data, dict):
            raise ValueError('task must be a mapping')
        allowed = {'key', 'cabal_id', 'role_id', 'acceptance_digest', 'depends_on',
                   'priority', 'duration_units', 'resources', 'package_digest',
                   'entrypoint_id', 'grant_id'}
        if set(data) - allowed:
            raise ValueError('unknown task specification fields')
        if not isinstance(data.get('depends_on'), list):
            raise ValueError('task depends_on must be an explicit list')
        if not isinstance(data.get('resources', {}), dict):
            raise ValueError('task resources must be a mapping')
        dependencies = tuple(data['depends_on'])
        if len(set(dependencies)) != len(dependencies) or not all(isinstance(x, str) and x for x in dependencies):
            raise ValueError('invalid dependency list')
        resources = tuple(sorted(data.get('resources', {}).items()))
        for k, v in resources:
            if not isinstance(k, str) or not k or type(v) is not int or v <= 0:
                raise ValueError('resource requests must be positive integers')
        for key in ('key', 'cabal_id', 'role_id', 'acceptance_digest'):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(f'missing task {key}')
        for key in ('package_digest', 'entrypoint_id', 'grant_id'):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(f'owner brief must pin {key} for each assignment')
        for key in ('priority', 'duration_units'):
            if type(data.get(key, 1 if key == 'duration_units' else 0)) is not int:
                raise ValueError(f'invalid {key}')
        if data.get('duration_units', 1) <= 0:
            raise ValueError('duration must be positive')
        return cls(key=data['key'], cabal_id=data['cabal_id'], role_id=data['role_id'],
                   acceptance_digest=data['acceptance_digest'], depends_on=dependencies,
                   priority=data.get('priority', 0), duration_units=data.get('duration_units', 1),
                   resources=resources, package_digest=data.get('package_digest', ''),
                   entrypoint_id=data.get('entrypoint_id', ''), grant_id=data.get('grant_id', ''))


@dataclass(frozen=True)
class FeatureBrief:
    acceptance_digest: str
    source_revision_id: str
    tasks: tuple[TaskSpec, ...]
    repair_of_task_id: str = ''
    failure_evidence_digest: str = ''

    @classmethod
    def parse(cls, body: Mapping[str, Any]) -> 'FeatureBrief':
        base = {'acceptance_digest', 'source_revision_id', 'tasks'}
        optional = {'repair_of_task_id', 'failure_evidence_digest'}
        if not isinstance(body, dict) or not base <= set(body) or set(body) - (base | optional):
            raise ValueError('feature brief requires acceptance_digest, source_revision_id and tasks')
        if (bool(body.get('repair_of_task_id')) != bool(body.get('failure_evidence_digest'))
                or any(not isinstance(body.get(k), str) for k in optional if k in body)):
            raise ValueError('repair provenance must identify failed task and exact QA evidence')
        for key in ('acceptance_digest', 'source_revision_id'):
            if not isinstance(body.get(key), str) or not body[key]:
                raise ValueError(f'feature brief {key} missing')
        if not isinstance(body.get('tasks'), list) or not body['tasks']:
            raise ValueError('feature brief must explicitly enumerate tasks')
        tasks = tuple(TaskSpec.parse(item) for item in body['tasks'])
        if len({t.key for t in tasks}) != len(tasks):
            raise ValueError('duplicate task key')
        return cls(body['acceptance_digest'], body['source_revision_id'], tasks,
                   body.get('repair_of_task_id',''), body.get('failure_evidence_digest',''))


@dataclass(frozen=True)
class PreparedAssignment:
    """Prepared assignment metadata, *not* a live browser or GitHub publish receipt."""
    project_id: str
    task_id: str
    feature_id: str
    lease_generation: int
    worker_id: str
    snapshot_id: str
    source_revision_id: str
    package_digest: str
    entrypoint_id: str
    grant_id: str
    acceptance_digest: str
    expires_at: str
