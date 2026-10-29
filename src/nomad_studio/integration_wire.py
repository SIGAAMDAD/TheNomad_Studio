"""The frozen wire ABI uses ``domain.operation``; several worker facades do not.

This explicit translation exists only at the authenticated gateway edge, AFTER
WG-B has authorized the *original* dotted operation against the live session.
The private component APIs remain unchanged. Unlisted operations fail closed.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from nomad_studio.contracts.v1 import CommandEnvelope, Component, Outcome, QueryEnvelope, VerifiedIdentity

# Worker APIs implemented during the one-wave use unqualified internal verbs.
# These sets are the only permitted edge translations. Keep absent operations absent.
PLAIN_COMMANDS: Mapping[str, frozenset[str]] = {
    'state': frozenset({'snapshot'}),
    'relay': frozenset({'ingest', 'ack', 'retry', 'dead_letter'}),
    'shift': frozenset({'start', 'next', 'record', 'checkpoint', 'observe_control', 'finish', 'yield', 'stop'}),
    'scheduler': frozenset({'enqueue', 'reserve', 'claim', 'release', 'reassign'}),
    'source': frozenset({'resolve', 'verify_base', 'check_paths', 'readback_ci', 'protect'}),
    'operations': frozenset({'hourly', 'daily', 'alert', 'recovery'}),
    'oversight': frozenset({'issue_notice', 'verify_notice', 'apply_notice', 'revoke_notice'}),
    'verification': frozenset({'contract_gate', 'load_run', 'fault_run', 'acceptance_report'}),
    'budget': frozenset({'grant', 'reserve', 'reconcile', 'quota', 'revoke', 'measure'}),
    'deployment': frozenset({'rollback'}),
}
PLAIN_QUERIES: Mapping[str, frozenset[str]] = {
    'state': frozenset({'health', 'schema', 'events'}),
    'relay': frozenset({'poll'}),
    'shift': frozenset({'status', 'receipts', 'capsule'}),
    'scheduler': frozenset({'ready'}),
    'source': frozenset({'resolve', 'protect'}),
    'operations': frozenset({'inspect', 'hourly', 'daily', 'health', 'recovery'}),
    'oversight': frozenset({'health', 'verify_notice', 'notice_status'}),
    'verification': frozenset({'contract_gate', 'acceptance_report'}),
    'budget': frozenset({'quota'}),
    'deployment': frozenset({'status', 'config_check', 'connector_probe'}),
}


class WireComponent:
    """Expose one private source facade using the frozen public dotted wire ABI."""

    def __init__(self, inner: Component):
        self.inner = inner
        self.name = inner.name

    def _verb(self, operation: str, allowed: Mapping[str, frozenset[str]]) -> str | None:
        prefix = self.name + '.'
        if not operation.startswith(prefix):
            return None
        verb = operation[len(prefix):]
        if not verb or '.' in verb:
            return None
        if self.name in allowed:
            return verb if verb in allowed[self.name] else None
        return operation

    def execute(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        if not isinstance(command, CommandEnvelope) or command.domain != self.name:
            return Outcome('DENIED', 'MALFORMED_REQUEST')
        verb = self._verb(command.operation, PLAIN_COMMANDS)
        if verb is None:
            return Outcome('DENIED', 'NOT_SUPPORTED')
        return self.inner.execute(replace(command, operation=verb), identity)

    def query(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        if not isinstance(query, QueryEnvelope) or query.domain != self.name:
            return Outcome('DENIED', 'MALFORMED_REQUEST')
        verb = self._verb(query.operation, PLAIN_QUERIES)
        if verb is None:
            return Outcome('DENIED', 'NOT_SUPPORTED')
        return self.inner.query(replace(query, operation=verb), identity)


def gateway_facades(components: Mapping[str, Component]) -> dict[str, WireComponent]:
    """No internal privileged adapter is ever added to this collection."""
    return {name: WireComponent(component) for name, component in components.items()}
