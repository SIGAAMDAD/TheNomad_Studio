"""P2-owned outbox consumer hook for CAB-J owner commands.

A command is never replayed on text assertion; CAB-H authenticates its stored
proof against its canonical request digest on every dispatch attempt. P2 owns
CLAIMED/ACKED/UNKNOWN transitions, checkpointing and backoff.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from nomad_studio.production.director.wire import Result, fail, valid_digest


class OwnerCommandOutboxHandler:
    def __init__(self, *, command_port: Any, identity_port: Any):
        self.commands = command_port
        self.identity = identity_port

    def handle(self, envelope: Mapping[str, Any]) -> Result:
        if not isinstance(envelope, Mapping) or envelope.get('domain') != 'production.commands' or envelope.get('event_type') != 'EXECUTE_OWNER_COMMAND':
            return fail('REJECTED', 'INVALID_INPUT')
        command = envelope.get('command')
        if not isinstance(command, Mapping) or not valid_digest(command.get('request_digest')):
            return fail('REJECTED', 'INVALID_INPUT')
        if envelope.get('idempotency_key') != f'owner:{command.get("command_id")}':
            return fail('REJECTED', 'IDEMPOTENCY_CONFLICT')
        identity = self.identity.authenticate_owner(command.get('auth_proof_ref'), command['request_digest'])
        if not isinstance(identity, Result) or identity.status != 'OK':
            return fail('REJECTED', 'UNAUTHENTICATED')
        return self.commands.execute_pending(command, identity)
