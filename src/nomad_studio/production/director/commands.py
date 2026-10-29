"""Frozen CommandPort; CAB-H authenticates, CAB-J records and routes.

Ingress only admits authenticated R9 OwnerCommand records. P2 dispatches
pending commands from its durable outbox, and re-verifies proof on replay.
Neither this service nor its CLI creates owner credentials.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any, Protocol
from .wire import Record, Result, digest, fail, ok, valid_digest, valid_id

OPERATIONS = frozenset({
    'studio.project.register', 'studio.project.set_active', 'studio.set_budget',
    'cabal.configure', 'cabal.inspect', 'production.submit_brief',
    'production.status', 'production.inspect', 'production.request_changes',
    'production.pause', 'production.resume', 'production.cancel',
    'bible.propose', 'bible.approve', 'effects.approve_protected', 'studio.status'
})
FIELDS = frozenset({'command_id','operation','owner_principal_id','request_digest','auth_proof_ref','idempotency_key','issued_at_us','project_id','feature_id'})
FEATURE_OPS = {'production.pause','production.resume','production.cancel', 'production.status', 'production.inspect','production.request_changes'}


class CommandService:
    """P2 must inject CAB-J-only writer, P2 outbox and CAB-H-auth Result."""
    def __init__(self, writer: Any, *, outbox: Any, status: Any, director: Any,
                 workflow: Any, payload_reader: Any, delegates: Mapping[str, Any] | None = None,
                 control: Any = None, clock_us: Any = None):
        self.writer = writer
        self.outbox = outbox
        self.status = status
        self.director = director
        self.workflow = workflow
        self.payload_reader = payload_reader  # trusted immutable gateway request by digest
        self.delegates = dict(delegates or {})
        self.control = control  # P2 scheduler/host lease fencing
        if clock_us is None:
            raise ValueError('trusted clock_us must be supplied by P2')
        self.clock_us = clock_us

    @staticmethod
    def _valid(command: Record) -> bool:
        if not isinstance(command, Mapping) or set(command) - FIELDS:
            return False
        if not all(valid_id(command.get(k)) for k in ('command_id','owner_principal_id','auth_proof_ref','idempotency_key')):
            return False
        if not valid_digest(command.get('request_digest')) or not isinstance(command.get('operation'), str) or command['operation'] not in OPERATIONS:
            return False
        if type(command.get('issued_at_us')) is not int or command['issued_at_us'] <= 0:
            return False
        for optional in ('project_id','feature_id'):
            if optional in command and not valid_id(command[optional]):
                return False
        if command['operation'] in FEATURE_OPS and ('project_id' not in command or 'feature_id' not in command):
            return False
        if command['operation'] == 'production.submit_brief' and 'project_id' not in command:
            return False
        return True

    @staticmethod
    def _authenticated(command: Record, identity: Result) -> bool:
        return (isinstance(identity, Result) and identity.status == 'OK' and identity.code == 'OK'
                and valid_id(identity.receipt_ref)
                and isinstance(identity.record, Mapping)
                and identity.record.get('principal_id') == command['owner_principal_id']
                and identity.record.get('principal_kind') == 'OWNER'
                and identity.receipt_ref == command['auth_proof_ref']
                and identity.record.get('action_digest') == command['request_digest']
                and identity.record.get('simulated') is False)

    def submit_owner_command(self, command: Record, verified_identity: Result) -> Result:
        if not self._valid(command):
            return fail('REJECTED', 'INVALID_INPUT')
        if not self._authenticated(command, verified_identity):
            return fail('REJECTED', 'UNAUTHENTICATED')
        with self.writer.transaction() as tx:
            if 'project_id' in command:
                project = tx.execute('SELECT owner_id FROM v1_projects WHERE project_id=?', (command['project_id'],)).fetchone()
                if command['operation'] != 'studio.project.register' and (project is None or project[0] != command['owner_principal_id']):
                    return fail('REJECTED', 'PERMISSION_DENIED')
            existing = tx.execute('SELECT command_id,request_digest,owner_principal_id,result_digest FROM v1_owner_commands WHERE idempotency_key=?', (command['idempotency_key'],)).fetchone()
            if existing:
                if existing[:3] != (command['command_id'],command['request_digest'],command['owner_principal_id']):
                    return fail('CONFLICT', 'IDEMPOTENCY_CONFLICT')
                if existing[3] != digest({'pending':True}):
                    return ok({'command_id':command['command_id'], 'processed_result_digest':existing[3]})
                return Result('ACCEPTED', 'ACCEPTED_PENDING', {'command_id':command['command_id']})
            if tx.execute('SELECT 1 FROM v1_owner_commands WHERE command_id=?', (command['command_id'],)).fetchone():
                return fail('CONFLICT','IDEMPOTENCY_CONFLICT')
            now = self.clock_us()
            if type(now) is not int or now <= 0:
                return fail('BLOCKED_LOCAL', 'LOCAL_RECOVERY_REQUIRED')
            tx.execute('INSERT INTO v1_owner_commands(command_id,project_id,feature_id,owner_principal_id,request_digest,result_digest,idempotency_key,created_at_us) VALUES(?,?,?,?,?,?,?,?)',
                       (command['command_id'], command.get('project_id'), command.get('feature_id'), command['owner_principal_id'], command['request_digest'], digest({'pending':True}), command['idempotency_key'], now))
            self.outbox.append(tx, {'project_id':command.get('project_id'), 'feature_id':command.get('feature_id'),
                                   'domain':'production.commands', 'event_type':'EXECUTE_OWNER_COMMAND',
                                   'command':dict(command), 'idempotency_key':f'owner:{command["command_id"]}'})
        return Result('ACCEPTED','ACCEPTED_PENDING',{'command_id':command['command_id']})

    def execute_pending(self, command: Record, fresh_identity: Result) -> Result:
        """Worker-side replay: P2 must first re-authenticate CAB-H proof.

        Called outside SQLite transaction; source command must still be admitted.
        P2 owns outbox ACK/UNKNOWN and retries. Every service effect is keyed to
        domain-specific identities, and pending provider outcomes require readback.
        """
        if not self._valid(command):
            return fail('REJECTED','INVALID_INPUT')
        if not self._authenticated(command, fresh_identity):
            return fail('REJECTED', 'UNAUTHENTICATED')
        with self.writer.transaction() as tx:
            row = tx.execute('SELECT request_digest,owner_principal_id,result_digest FROM v1_owner_commands WHERE command_id=? AND idempotency_key=?',
                             (command['command_id'], command['idempotency_key'])).fetchone()
            if row is None or row[:2] != (command['request_digest'], command['owner_principal_id']):
                return fail('REJECTED','EVIDENCE_INVALID')
            if 'project_id' in command and command['operation'] != 'studio.project.register':
                owner = tx.execute('SELECT owner_id FROM v1_projects WHERE project_id=?',(command['project_id'],)).fetchone()
                if not owner or owner[0] != command['owner_principal_id']:
                    return fail('REJECTED','PERMISSION_DENIED')
            if row[2] != digest({'pending':True}):
                return ok({'command_id':command['command_id'], 'processed_result_digest':row[2]})
        operation = command['operation']
        owner, feature = command['owner_principal_id'], command.get('feature_id')
        if operation == 'production.submit_brief':
            outcome = self.workflow.submit(command, fresh_identity)
        elif operation in ('production.status','production.inspect'):
            outcome = self.status.get_feature(owner, feature)
        elif operation == 'studio.status':
            outcome = self.status.get_studio(owner, command.get('project_id'))
        elif operation in ('production.pause','production.resume','production.cancel','production.request_changes'):
            current = self.director.get_feature_status(feature, owner)
            if current.status != 'OK':
                return current
            # Request content must be fetched from an immutable trusted gateway,
            # and must be exactly the request_digest authenticated by CAB-H.
            payload = self.payload_reader.read_request(command['command_id'], command['request_digest'])
            if not isinstance(payload, Mapping) or digest(payload) != command['request_digest']:
                return fail('REJECTED','EVIDENCE_INVALID')
            expected = payload.get('expected_revision')
            if type(expected) is not int or expected != current.record['revision']:
                return fail('CONFLICT','REVISION_CONFLICT')
            if operation == 'production.request_changes':
                outcome = self.workflow.replan_changes(feature, owner, payload, fresh_identity)
            else:
                action = operation.rsplit('.',1)[-1]
                if action in ('pause','cancel'):
                    if self.control is None:
                        return fail('BLOCKED_LOCAL', 'PROVIDER_NOT_VERIFIED')
                    fence = self.control.fence_feature(feature, current.record['revision'], action)
                    if not isinstance(fence,Result) or fence.status != 'OK' or fence.code != 'OK' or not valid_id(fence.receipt_ref):
                        return fail('BLOCKED_LOCAL', 'LOCAL_RECOVERY_REQUIRED')
                state = 'PAUSED' if action == 'pause' else 'CANCELLED' if action == 'cancel' else self.director.resume_target(feature)
                if not state:
                    return fail('CONFLICT', 'REVISION_CONFLICT')
                outcome = self.director.advance_run(feature, expected, state, {'reason':f'owner_{action}', 'command_id':command['command_id']})
        elif operation in self.delegates:
            outcome = self.delegates[operation](command, fresh_identity)
        else:
            return fail('BLOCKED_LOCAL','PROVIDER_NOT_VERIFIED')
        if not isinstance(outcome, Result):
            return fail('REJECTED','EVIDENCE_INVALID')
        if outcome.status in ('OK','ACCEPTED','REJECTED','CONFLICT','NOT_FOUND'):
            with self.writer.transaction() as tx:
                tx.execute('UPDATE v1_owner_commands SET result_digest=? WHERE command_id=? AND request_digest=?',
                           (digest({'status':outcome.status,'code':outcome.code,'record':outcome.record,'receipt_ref':outcome.receipt_ref}), command['command_id'],command['request_digest']))
        return outcome

    def get_feature_status(self, owner_id: str, feature_id: str) -> Result:
        """Internal-only owner-scoped query; P2 gateway must authenticate caller."""
        return self.status.get_feature(owner_id,feature_id)

    def get_studio_status(self, owner_id: str, project_id: str | None) -> Result:
        """Internal-only owner-scoped query; P2 gateway must authenticate caller."""
        return self.status.get_studio(owner_id,project_id)
