"""WG-A value types. No database connections or owner authentication in this module."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


class GovernanceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Principal:
    principal_id: str
    issuer: str
    kind: str  # human, service, worker; asserted by trusted J adapter only


@dataclass(frozen=True)
class Bible:
    bible_id: str
    kind: str
    title: str


@dataclass(frozen=True)
class Clause:
    clause_key: str
    classification: str
    text_digest: str
    scope_digest: str
    provenance_digest: str
    status: str
    text: str
    source: dict[str, Any]
    impacted_task_ids: tuple[str, ...] = ()
    impacted_artifact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fact:
    fact_id: str
    subject_key: str
    predicate: str
    value_digest: str
    evidence_digest: str
    status: str
    evidence: dict[str, Any]
    value: Any


@dataclass(frozen=True)
class Manifest:
    clauses: tuple[Clause, ...]
    facts: tuple[Fact, ...]


@dataclass(frozen=True)
class Intent:
    intent_id: str
    bible_id: str
    request_id: str
    content_digest: str
    manifest_digest: str
    expected_edition_id: str | None
    state: str
    sequence: int


@dataclass(frozen=True)
class Edition:
    edition_id: str
    bible_id: str
    kind: str
    content_digest: str
    manifest_digest: str
    owner_decision_id: str
    publication_effect_digest: str
    manifest: Manifest


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    design_bible_id: str
    design_edition_id: str
    technical_bible_id: str
    technical_edition_id: str
    policy_digest: str
    manifest_digest: str


@dataclass(frozen=True)
class Decision:
    decision_id: str
    subject_type: str
    subject_id: str
    action: str
    effect_digest: str
    snapshot_id: str | None
    requester_principal_id: str
    state: str = 'pending'
    revision: int = 1
    verified_owner_principal_id: str | None = None
    proof_digest: str | None = None


@dataclass(frozen=True)
class Rule:
    rule_id: str
    subject_type: str
    action: str
    scope_digest: str
    disposition: str


@dataclass(frozen=True)
class Policy:
    policy_id: str
    revision: int
    policy_digest: str
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Effect:
    effect_id: str
    subject_type: str
    subject_id: str
    action: str
    effect_digest: str
    snapshot_id: str | None
    decision_id: str | None
    request_id: str


@dataclass(frozen=True)
class BusinessEvent:
    event_type: str
    subject_id: str
    payload_digest: str
    actor_principal_id: str


@dataclass
class ProjectState:
    """Serializable business projection; only WG-C may physically persist it."""
    project_id: str
    bibles: dict[str, Bible] = field(default_factory=dict)
    intents: dict[str, Intent] = field(default_factory=dict)
    editions: dict[str, Edition] = field(default_factory=dict)
    active: dict[str, tuple[str, str]] = field(default_factory=dict)
    snapshots: dict[str, Snapshot] = field(default_factory=dict)
    current_snapshot_id: str | None = None
    proposals: dict[str, Decision] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    consumed_decisions: dict[str, str] = field(default_factory=dict)
    effects: dict[str, Effect] = field(default_factory=dict)
    policy: Policy | None = None
    grants: set[tuple[str, str]] = field(default_factory=set)
    # A's user-level request idempotency; WG-C persists equivalent idempotency records.
    requests: dict[tuple[str, str], tuple[str, Any]] = field(default_factory=dict)
    sequence: int = 0
    events: list[BusinessEvent] = field(default_factory=list)
