"""WG-M project bootstrap and configuration through C's single APSW writer.

First-project creation uses the frozen (principal_id, request_id) bootstrap key.
Project configuration uses the WG-M scoped request journal. All project and
grant mutations commit atomically on the existing physical SQLite connection.
"""
from __future__ import annotations

import hashlib
from typing import Any

from studio.app.errors import StudioError
from studio.app.projects import Project
from studio.storage.workforce import WorkforceStorage, WorkforceFault


class ProjectAPSWStore:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError('C workforce writer required')
        self.writer = writer

    @staticmethod
    def _record(row: dict | None) -> Project | None:
        return (Project(row['project_id'], row['name'], row['revision'], row['state'])
                if row is not None else None)

    def bootstrap_project(self, *, principal_id: str, request_id: str,
                          request_body_sha256: str, name: str,
                          charter_digest: str) -> Project:
        if not all(isinstance(s, str) and s for s in (
                principal_id, request_id, name, charter_digest)):
            raise StudioError('invalid', 'project identity/charter missing')
        if len(request_body_sha256) != 64 or any(
                s not in '0123456789abcdef' for s in request_body_sha256):
            raise StudioError('invalid', 'invalid bootstrap digest')
        project_id = 'project_' + hashlib.sha256(
            (principal_id + ':' + request_id).encode('utf-8')).hexdigest()[:32]
        with self.writer._transaction(write=True) as tx:
            principal = tx.one(
                'SELECT kind FROM principals WHERE principal_id=?', (principal_id,))
            if principal is None or principal['kind'] != 'human':
                raise StudioError('requires_owner', 'verified human owner required')
            prior = tx.one(
                'SELECT payload_digest,status,result_project_id FROM bootstrap_idempotency '
                'WHERE principal_id=? AND request_id=?', (principal_id, request_id))
            if prior is not None:
                if prior['payload_digest'] != request_body_sha256:
                    raise StudioError('conflict', 'bootstrap request ID changed')
                if prior['status'] != 'finished':
                    raise StudioError('unknown_effect', 'bootstrap is unresolved')
                row = tx.one(
                    'SELECT project_id,name,revision,state FROM projects WHERE project_id=?',
                    (prior['result_project_id'],))
                if row is None:
                    raise StudioError('unknown_effect', 'bootstrap result unavailable')
                return self._record(row)
            tx.change(
                "INSERT INTO bootstrap_idempotency(principal_id,request_id,payload_digest,status) "
                "VALUES(?,?,?,'started')",
                (principal_id, request_id, request_body_sha256))
            tx.change(
                "INSERT INTO projects(project_id,name,charter_digest,state) VALUES(?,?,?,'draft')",
                (project_id, name, charter_digest))
            tx.change(
                "INSERT INTO project_grants(project_id,principal_id,grant_code) VALUES(?,?,'owner')",
                (project_id, principal_id))
            tx.change(
                'INSERT INTO audit_events(project_id,event_seq,event_type,subject_id,'
                'payload_digest,actor_principal_id) VALUES(?,?,?,?,?,?)',
                (project_id, 1, 'project.created', project_id,
                 request_body_sha256, principal_id))
            tx.change(
                "UPDATE bootstrap_idempotency SET status='finished',result_project_id=? "
                "WHERE principal_id=? AND request_id=?",
                (project_id, principal_id, request_id))
            return Project(project_id, name, 1, 'draft')

    def get_project(self, *, project_id: str, principal_id: str) -> Project | None:
        with self.writer._lock:
            cursor = self.writer._connection.execute(
                'SELECT p.project_id,p.name,p.revision,p.state FROM projects p '
                'JOIN project_grants g ON g.project_id=p.project_id '
                'WHERE p.project_id=? AND g.principal_id=? '
                'GROUP BY p.project_id', (project_id, principal_id))
            row = cursor.fetchone()
            return Project(*row) if row is not None else None

    def list_granted_projects(self, *, principal_id: str, limit: int) -> list[Project]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StudioError('invalid', 'project list bound invalid')
        with self.writer._lock:
            cursor = self.writer._connection.execute(
                'SELECT p.project_id,p.name,p.revision,p.state FROM projects p '
                'JOIN project_grants g ON g.project_id=p.project_id '
                'WHERE g.principal_id=? GROUP BY p.project_id ORDER BY p.project_id LIMIT ?',
                (principal_id, limit))
            return [Project(*row) for row in cursor]

    def configure_project(self, *, project_id: str, principal_id: str,
                          request_id: str, request_body_sha256: str,
                          config_digest: str, expected_revision: int) -> Project:
        if type(expected_revision) is not int or expected_revision < 1:
            raise StudioError('invalid', 'project revision invalid')

        def write(tx):
            existing = tx.one(
                'SELECT p.project_id,p.name,p.revision,p.state '
                'FROM projects p JOIN project_grants g ON p.project_id=g.project_id '
                'WHERE p.project_id=? AND g.principal_id=? LIMIT 1',
                (project_id, principal_id))
            if existing is None:
                raise StudioError('forbidden', 'project access denied')
            if existing['revision'] != expected_revision:
                raise StudioError('stale_revision', 'project revision changed')
            if tx.change(
                    "UPDATE projects SET revision=revision+1,state='active' "
                    'WHERE project_id=? AND revision=?',
                    (project_id, expected_revision)) != 1:
                raise StudioError('stale_revision', 'project configuration CAS failed')
            tx.change(
                'INSERT INTO project_profiles(project_id,profile_id,config_digest,revision) '
                'VALUES(?,?,?,?) ON CONFLICT(project_id,profile_id) DO UPDATE SET '
                'config_digest=excluded.config_digest,revision=excluded.revision',
                (project_id, 'active', config_digest, expected_revision + 1))
            next_seq = tx.one(
                'SELECT COALESCE(MAX(event_seq),0)+1 AS seq FROM audit_events '
                'WHERE project_id=?', (project_id,))['seq']
            tx.change(
                'INSERT INTO audit_events(project_id,event_seq,event_type,subject_id,'
                'payload_digest,actor_principal_id) VALUES(?,?,?,?,?,?)',
                (project_id, next_seq, 'project.configured', project_id,
                 request_body_sha256, principal_id))
            return {'project_id':project_id, 'name':existing['name'],
                    'revision':expected_revision+1, 'state':'active'}
        try:
            outcome = self.writer.command(project_id, 'M.configureProject',
                                          request_id, request_body_sha256, write)
        except WorkforceFault as exc:
            error = {'unknown':'unknown_effect','conflict':'conflict',
                     'invalid':'invalid'}.get(exc.code,'internal')
            raise StudioError(error, str(exc)) from exc
        return self._record(outcome)
