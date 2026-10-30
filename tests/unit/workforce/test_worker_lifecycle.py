"""SQLite reference checks of real first-use worker state, not browser-provider tests."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from studio.workforce import Workforce, WorkforceFault

SCHEMA=Path(__file__).resolve().parents[3]/'schema/initial_schema.sql'


class Transaction:
    def __init__(self, db):self.db=db
    def one(self, sql, params=()):
        row=self.db.execute(sql,params).fetchone()
        return dict(row) if row else None
    def all(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql,params)]
    def change(self, sql, params=()):
        return self.db.execute(sql,params).rowcount


class ReferenceStorage:
    def __init__(self):
        self.db=sqlite3.connect(':memory:',isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.executescript(SCHEMA.read_text())
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute("INSERT INTO principals VALUES ('owner','trusted','owner','human')")
        self.db.execute("INSERT INTO projects(project_id,name,charter_digest,state) VALUES ('p','project','charter','active')")
        self.db.execute("INSERT INTO specialist_roles(role_id,role_revision,role_manifest_digest,state) VALUES ('role','1','digest','ready')")
    def read(self, project_id, fn):return fn(Transaction(self.db))
    def command(self, project_id, operation, request_id, payload_sha256, fn):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT payload_digest,status,result_digest FROM idempotency WHERE project_id=? AND action_owner=? AND request_id=?',
                                 (project_id,operation,request_id)).fetchone()
            if row:
                if row['payload_digest'] != payload_sha256 or row['status'] != 'finished':
                    raise WorkforceFault('conflict','idempotency conflict')
                result=json.loads(row['result_digest'])
            else:
                self.db.execute("INSERT INTO idempotency VALUES (?,?,?,?,'started',NULL)",
                                (project_id,operation,request_id,payload_sha256))
                result=fn(Transaction(self.db))
                self.db.execute("UPDATE idempotency SET status='finished',result_digest=? WHERE project_id=? AND action_owner=? AND request_id=?",
                                (json.dumps(result,sort_keys=True),project_id,operation,request_id))
            self.db.execute('COMMIT');return result
        except BaseException:
            self.db.execute('ROLLBACK');raise


@pytest.fixture
def rig():
    store=ReferenceStorage()
    def authorize(actor, project_id, action):
        if project_id!='p' or actor.get('principal_id')!='owner':
            raise WorkforceFault('forbidden','wrong principal/project')
    workflow=Workforce(store,require_action=authorize)
    try:yield store,workflow,{'principal_id':'owner'}
    finally:store.db.close()


def test_worker_is_persistent_across_multiple_shifts(rig):
    store,worker,actor=rig
    result=worker.register(actor,'p',dict(request_id='enroll',worker_id='w',
                                    browser_chat_ref='existing-chat',role_id='role'))
    assert result['state']=='registered'
    assert worker.register(actor,'p',dict(request_id='enroll',worker_id='w',
                                           browser_chat_ref='existing-chat',role_id='role'))==result
    shift=worker.begin_shift(actor,'p',dict(request_id='start',worker_id='w',shift_id='shift-1'))
    assert shift['state']=='awaiting_hello'
    assert store.db.execute("SELECT COUNT(*) FROM worker_shifts WHERE state='active'").fetchone()[0]==0
    hello=dict(request_id='hello',worker_id='w',shift_id='shift-1',receipt_commit_sha='a'*40,
               receipt_digest='b'*64)
    with pytest.raises(WorkforceFault,match='verified HELLO'):
        worker.observe_hello(actor,'p',hello,verify=lambda evidence:False)
    assert worker.observe_hello(actor,'p',hello,verify=lambda evidence:True)['state']=='active'
    with pytest.raises(WorkforceFault,match='another shift'):
        worker.begin_shift(actor,'p',dict(request_id='new',worker_id='w',shift_id='shift-2'))
    final=dict(request_id='end',shift_id='shift-1',receipt_commit_sha='c'*40,receipt_digest='d'*64)
    assert worker.end_shift(actor,'p',final,verify=lambda evidence:True)['state']=='finished'
    assert worker.begin_shift(actor,'p',dict(request_id='new',worker_id='w',shift_id='shift-2'))['state']=='awaiting_hello'
    assert store.db.execute('SELECT COUNT(*) FROM worker_conversations').fetchone()[0]==1
    assert store.db.execute('SELECT COUNT(*) FROM worker_shifts').fetchone()[0]==2


def test_enrollment_requires_authenticated_scope_and_unique_chat(rig):
    store,worker,actor=rig
    args=dict(request_id='a',worker_id='w',browser_chat_ref='chat',role_id='role')
    with pytest.raises(WorkforceFault):worker.register({'principal_id':'stranger'},'p',args)
    worker.register(actor,'p',args)
    with pytest.raises(sqlite3.IntegrityError):
        worker.register(actor,'p',dict(request_id='b',worker_id='another',browser_chat_ref='chat',role_id='role'))
    assert store.db.execute('SELECT COUNT(*) FROM worker_conversations').fetchone()[0]==1


def test_wrong_project_and_missing_observation_cannot_activate(rig):
    _,worker,actor=rig
    worker.register(actor,'p',dict(request_id='a',worker_id='w',browser_chat_ref='chat',role_id='role'))
    with pytest.raises(WorkforceFault):
        worker.begin_shift(actor,'other',dict(request_id='b',worker_id='w',shift_id='s'))
    with pytest.raises(WorkforceFault,match='not awaiting HELLO'):
        worker.observe_hello(actor,'p',dict(request_id='c',worker_id='w',shift_id='nonexistent',
                           receipt_commit_sha='a'*40,receipt_digest='b'*64),verify=lambda _:True)


def test_git_relay_validates_shift_and_recipient(rig):
    from studio.integrations.github import GitHub, GitHubChannel
    from studio.integrations.common import ProjectBinding, IntegrationError
    from studio.workforce import canonical
    github=GitHub(object(),lambda:'token',GitHubChannel('p','owner','repository','studio/comms'),
                  ProjectBinding('p','account'))
    command=dict(project_id='p',worker_id='w',shift_id='shift',assignment_id='a',
                 command_id='cmd',kind='ASSIGN',payload_digest='a'*64,source_revision='commit')
    path='.nomad/relays/p/mail/w/cmd.json'
    github._validate_path(path,canonical(command))
    with pytest.raises(IntegrationError):
        github._validate_path('.nomad/relays/p/mail/other/cmd.json',canonical(command))
    with pytest.raises(IntegrationError):
        github._validate_path(path,canonical(dict(command,source_revision='')))
    with pytest.raises(IntegrationError):
        github._validate_path(path,canonical(dict(command,command_id='other')))
