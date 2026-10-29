"""Frozen, minimal module and transaction interfaces for single-wave implementers.

Nothing here authenticates client claims. Gateway establishes VerifiedIdentity only
from independently verified transport / launch capability and live server policy.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence, TypeVar

T = TypeVar('T')

@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    version: str
    request_id: str
    domain: str
    operation: str
    idempotency_key: str
    actor_claim: str | None = None
    worker_id: str | None = None
    shift_id: str | None = None
    job_id: str | None = None
    assignment_epoch: int | None = None
    source_sha: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class QueryEnvelope:
    version: str
    request_id: str
    domain: str
    operation: str
    worker_id: str | None = None
    shift_id: str | None = None
    job_id: str | None = None
    cursor: str | None = None
    limit: int = 100
    payload: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    principal_id: str
    credential_id: str
    issuer_id: str
    authenticated_via: str
    scopes: frozenset[str]
    expires_at_us: int
    current_authority_epoch: int

@dataclass(frozen=True, slots=True)
class Outcome:
    state: str
    code: str
    receipt_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: str
    domain: str
    kind: str
    actor_principal_id: str
    issued_at_us: int
    correlation_id: str | None = None
    causation_id: str | None = None
    job_id: str | None = None
    shift_id: str | None = None
    assignment_epoch: int | None = None
    payload_sha256: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

class StateReader(Protocol):
    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Mapping[str, Any] | None: ...
    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> Sequence[Mapping[str, Any]]: ...

class StateTx(StateReader, Protocol):
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int: ...
    def append_event(self, event: DomainEvent) -> str: ...

class StateStore(Protocol):
    def read(self, callback: Callable[[StateReader], T]) -> T: ...
    def transact(self, callback: Callable[[StateTx], T], *, idempotency_key: str | None = None) -> T: ...
    def close(self) -> None: ...

class Component(Protocol):
    name: str
    def execute(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome: ...
    def query(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome: ...
