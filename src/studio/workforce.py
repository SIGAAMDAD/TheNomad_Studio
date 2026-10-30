"""Registered browser conversations, independently started shifts and durable dispatch.

The coordinator never invokes a model. A human starts an actual ChatGPT
response; GitHub observations establish when it is active. Assignments are
queued for that active response rather than creating a task-specific chat.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol


class WorkforceFault(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('utf-8')


def digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def command_id(project_id: str, task_id: str, generation: int, shift_id: str) -> str:
    return 'cmd_' + hashlib.sha256(canonical({'project': project_id, 'task': task_id,
                                               'generation': generation, 'shift': shift_id})).hexdigest()


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise WorkforceFault('invalid', f'{field} must be nonempty')
    return value


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Transaction(Protocol):
    def one(self, sql: str, params: tuple = ()) -> Mapping[str, Any] | None: ...
    def all(self, sql: str, params: tuple = ()) -> list[Mapping[str, Any]]: ...
    def change(self, sql: str, params: tuple = ()) -> int: ...


class Storage(Protocol):
    def command(self, project_id: str, operation: str, request_id: str,
                payload_sha256: str, fn: Callable[[Transaction], dict]) -> dict: ...
    def read(self, project_id: str, fn: Callable[[Transaction], Any]) -> Any: ...


class VerifiedGitHub(Protocol):
    def publish(self, path: str, body: bytes, *, expected_digest: str,
                effect_intent_recorded: bool) -> Any: ...
    def observe_exact(self, path: str) -> Any: ...


class Workforce:
    """Transaction-only writes. Caller supplies project authorization before entry."""
    def __init__(self, storage: Storage, *, require_action: Callable[[Mapping[str, Any], str, str], None]):
        self.storage = storage
        self.require_action = require_action

    def register(self, actor: Mapping[str, Any], project_id: str, request: Mapping[str, Any]) -> dict:
        self.require_action(actor, project_id, 'registerWorker')
        if set(request) != {'request_id', 'worker_id', 'browser_chat_ref', 'role_id'}:
            raise WorkforceFault('invalid', 'unexpected registration fields')
        for field in request: _nonempty(request[field], field)
        principal = _nonempty(actor.get('principal_id'), 'principal_id')
        def apply(tx: Transaction) -> dict:
            # A browser conversation is registered ONCE; no task or lease is assigned at enrollment.
            if tx.change('''INSERT INTO worker_conversations(project_id,worker_id,principal_id,role_id,browser_chat_ref,state)
                         VALUES (?,?,?,?,?,'registered')''',
                         (project_id, request['worker_id'], principal, request['role_id'], request['browser_chat_ref'])) != 1:
                raise WorkforceFault('conflict', 'worker registration failed')
            return {'worker_id': request['worker_id'], 'state': 'registered'}
        return self.storage.command(project_id, 'workers.register', request['request_id'], digest(request), apply)

    def begin_shift(self, actor: Mapping[str, Any], project_id: str, request: Mapping[str, Any]) -> dict:
        self.require_action(actor, project_id, 'beginShift')
        if set(request) != {'request_id', 'worker_id', 'shift_id'}:
            raise WorkforceFault('invalid', 'unexpected shift fields')
        for field in request: _nonempty(request[field], field)
        def apply(tx: Transaction) -> dict:
            worker = tx.one("SELECT state FROM worker_conversations WHERE project_id=? AND worker_id=?",
                            (project_id, request['worker_id']))
            if worker is None or worker['state'] != 'registered':
                raise WorkforceFault('conflict', 'worker not enrolled')
            if tx.one("""SELECT shift_id FROM worker_shifts WHERE project_id=? AND worker_id=?
                         AND state IN ('awaiting_hello','active','stopping')""",
                      (project_id, request['worker_id'])):
                raise WorkforceFault('conflict', 'another shift is pending or active')
            tx.change("""INSERT INTO worker_shifts(project_id,shift_id,worker_id,state,created_at)
                         VALUES (?,?,?,'awaiting_hello',?)""",
                      (project_id, request['shift_id'], request['worker_id'], _utc()))
            return {'shift_id': request['shift_id'], 'worker_id': request['worker_id'], 'state': 'awaiting_hello'}
        return self.storage.command(project_id, 'workers.begin_shift', request['request_id'], digest(request), apply)

    def observe_hello(self, actor: Mapping[str, Any], project_id: str, request: Mapping[str, Any],
                      *, verify: Callable[[Mapping[str, Any]], bool]) -> dict:
        self.require_action(actor, project_id, 'observeWorker')
        if set(request) != {'request_id', 'worker_id', 'shift_id', 'receipt_commit_sha', 'receipt_digest'}:
            raise WorkforceFault('invalid', 'unexpected HELLO fields')
        for field in request: _nonempty(request[field], field)
        if len(request['receipt_digest']) != 64 or len(request['receipt_commit_sha']) != 40:
            raise WorkforceFault('invalid', 'invalid GitHub receipt identity')
        # The trusted verifier must prove exact GitHub bytes/recipient/shift; caller-owned data is untrusted.
        if not verify(request):
            raise WorkforceFault('forbidden', 'no verified HELLO in the existing conversation')
        def apply(tx: Transaction) -> dict:
            if tx.change("""UPDATE worker_shifts SET state='active',observed_at=?,hello_commit_sha=?,hello_digest=?
                         WHERE project_id=? AND worker_id=? AND shift_id=? AND state='awaiting_hello'""",
                         (_utc(), request['receipt_commit_sha'], request['receipt_digest'],
                          project_id, request['worker_id'], request['shift_id'])) != 1:
                raise WorkforceFault('conflict', 'shift not awaiting HELLO')
            return {'shift_id': request['shift_id'], 'state': 'active'}
        return self.storage.command(project_id, 'workers.observe_hello', request['request_id'], digest(request), apply)

    def end_shift(self, actor: Mapping[str, Any], project_id: str, request: Mapping[str, Any],
                  *, verify: Callable[[Mapping[str, Any]], bool]) -> dict:
        self.require_action(actor, project_id, 'endShift')
        if set(request) != {'request_id', 'shift_id', 'receipt_commit_sha', 'receipt_digest'}:
            raise WorkforceFault('invalid', 'unexpected final receipt fields')
        for field in request: _nonempty(request[field], field)
        if len(request['receipt_digest']) != 64 or len(request['receipt_commit_sha']) != 40 or not verify(request):
            raise WorkforceFault('forbidden', 'final shift receipt not verified')
        def apply(tx: Transaction) -> dict:
            shift = tx.one("SELECT worker_id,state FROM worker_shifts WHERE project_id=? AND shift_id=?",
                           (project_id, request['shift_id']))
            if shift is None or shift['state'] not in ('active','stopping'):
                raise WorkforceFault('conflict','shift not active')
            pending = tx.one("""SELECT command_id FROM command_outbox WHERE project_id=? AND shift_id=?
                         AND state NOT IN ('completed','canceled') LIMIT 1""",(project_id,request['shift_id']))
            if pending:
                raise WorkforceFault('conflict','shift has unverified pending commands')
            tx.change("""UPDATE worker_shifts SET state='finished',finished_at=?,final_commit_sha=?,final_digest=?
                         WHERE project_id=? AND shift_id=?""", (_utc(),request['receipt_commit_sha'],request['receipt_digest'],
                                                                project_id,request['shift_id']))
            return {'shift_id': request['shift_id'], 'state':'finished'}
        return self.storage.command(project_id, 'workers.end_shift', request['request_id'], digest(request), apply)

    def pending(self, actor: Mapping[str, Any], project_id: str) -> list[dict]:
        self.require_action(actor, project_id, 'publishCommands')
        return self.storage.read(project_id, lambda tx: [dict(r) for r in tx.all("""
          SELECT command_id,worker_id,shift_id,remote_path,envelope_digest,envelope_json,state
          FROM command_outbox WHERE project_id=? AND state IN ('queued','unknown')
          ORDER BY created_at,command_id LIMIT 100""", (project_id,))])

    def publish_pending(self, actor: Mapping[str, Any], project_id: str,
                        *, provider: VerifiedGitHub) -> dict[str, int]:
        """Publish after DB commit; remote ambiguity stays UNKNOWN until exact readback."""
        results={'published':0,'unknown':0,'rejected':0}
        for record in self.pending(actor,project_id):
            body=record['envelope_json'].encode('utf-8')
            if hashlib.sha256(body).hexdigest()!=record['envelope_digest']:
                results['rejected'] += 1
                continue
            remote=None
            try:
                remote=provider.observe_exact(record['remote_path'])
                if remote is None:
                    remote=provider.publish(record['remote_path'], body,
                                            expected_digest=record['envelope_digest'],
                                            effect_intent_recorded=True)
                if (remote is None or not getattr(remote,'verified',False) or
                    getattr(remote,'path',None)!=record['remote_path'] or
                    getattr(remote,'body_sha256',None)!=record['envelope_digest'] or
                    getattr(remote,'body',None)!=body or
                    len(getattr(remote,'commit_sha',''))!=40):
                    raise WorkforceFault('unknown','unverified remote publication')
            except Exception:
                results['unknown']+=1
                self._set_publication(project_id,record['command_id'],'unknown',None)
                continue
            self._set_publication(project_id,record['command_id'],'published',remote.commit_sha)
            results['published']+=1
        return results

    def _set_publication(self, project_id: str, command: str, state: str, remote_sha: str | None) -> None:
        def apply(tx:Transaction)->dict:
            row=tx.one('SELECT envelope_digest,state,remote_commit_sha FROM command_outbox WHERE project_id=? AND command_id=?', (project_id,command))
            if row is None or row['state'] not in ('queued','unknown','published'):
                raise WorkforceFault('conflict','command mutated during publication')
            if row['state']=='published' and row['remote_commit_sha']!=remote_sha:
                raise WorkforceFault('conflict','remote commit diverges')
            tx.change('''UPDATE command_outbox SET state=?,remote_commit_sha=? WHERE project_id=? AND command_id=?''',
                      (state,remote_sha,project_id,command))
            return {'command_id':command,'state':state}
        self.storage.command(project_id,'workers.publish',f'{command}:{state}',
                             hashlib.sha256(f'{command}:{state}:{remote_sha}'.encode()).hexdigest(),apply)

    def observe_command_receipt(self, actor: Mapping[str, Any], project_id: str,
                                request: Mapping[str, Any],
                                *, verify: Callable[[Mapping[str, Any]], bool]) -> dict:
        """No completion without a validated, addressed, immutable GitHub receipt."""
        self.require_action(actor,project_id,'observeCommand')
        required={'request_id','receipt_id','command_id','worker_id','shift_id',
                  'kind','result_digest','receipt_commit_sha'}
        if set(request)!=required:
            raise WorkforceFault('invalid','unexpected command receipt fields')
        for field in required: _nonempty(request[field],field)
        if (len(request['result_digest'])!=64 or
                len(request['receipt_commit_sha'])!=40 or
                not verify(request)):
            raise WorkforceFault('forbidden','unverified command receipt')
        def apply(tx:Transaction)->dict:
            cmd=tx.one('''SELECT state,worker_id,shift_id,assignment_id FROM command_outbox
                          WHERE project_id=? AND command_id=?''', (project_id,request['command_id']))
            if not cmd or cmd['state'] not in ('published','observed'):
                raise WorkforceFault('conflict','command has no verified GitHub publication')
            if (cmd['worker_id'],cmd['shift_id']) != (request['worker_id'],request['shift_id']):
                raise WorkforceFault('forbidden','receipt worker or shift mismatch')
            tx.change('''INSERT INTO command_receipts(project_id,receipt_id,command_id,worker_id,shift_id,
                          kind,result_digest,remote_commit_sha,observed_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                      (project_id,request['receipt_id'],request['command_id'],request['worker_id'],
                       request['shift_id'],request['kind'],request['result_digest'],
                       request['receipt_commit_sha'],_utc()))
            state='completed' if request['kind']=='completed' else 'observed'
            tx.change('''UPDATE command_outbox SET state=? WHERE project_id=? AND command_id=?''',
                      (state,project_id,request['command_id']))
            if state=='completed':
                tx.change("""UPDATE shift_assignments SET state='submitted' WHERE project_id=?
                             AND assignment_id=? AND state IN ('assigned','observed')""",
                          (project_id,cmd['assignment_id']))
            return {'command_id':request['command_id'],'state':state}
        return self.storage.command(project_id,'workers.observe_receipt',request['request_id'],digest(request),apply)
