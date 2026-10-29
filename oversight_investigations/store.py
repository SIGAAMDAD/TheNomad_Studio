"""Deterministic local atomic ledger for synthetic tests; NOT encrypted case storage."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from threading import RLock
from typing import Callable, TypeVar

from .model import Case, Conflict, IntegrityFailure, LedgerEntry

T = TypeVar('T')
ZERO = '0' * 64


def canonical(value: object) -> bytes:
    def normalize(v: object) -> object:
        if isinstance(v, datetime):
            return v.astimezone(timezone.utc).isoformat()
        if isinstance(v, tuple):
            return [normalize(x) for x in v]
        if isinstance(v, dict):
            return {k: normalize(x) for k, x in v.items()}
        if isinstance(v, list):
            return [normalize(x) for x in v]
        return v
    return json.dumps(normalize(value), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True).encode('utf-8')


def digest(value: object) -> str:
    return sha256(canonical(value)).hexdigest()


def append_entry(entries: list[LedgerEntry], entry_id: str, kind: str,
                 actor_id: str, occurred_at: datetime, metadata: object,
                 state: str) -> None:
    previous = entries[-1].record_sha256 if entries else ZERO
    metadata_digest = digest(metadata)
    core = dict(entry_id=entry_id, kind=kind, actor_id=actor_id,
                occurred_at=occurred_at, metadata_sha256=metadata_digest,
                previous_sha256=previous, state=state)
    entries.append(LedgerEntry(**core, record_sha256=digest(core)))


def assert_chain(entries: list[LedgerEntry]) -> None:
    previous = ZERO
    for item in entries:
        core = {key: value for key, value in asdict(item).items()
                if key != 'record_sha256'}
        if item.previous_sha256 != previous or digest(core) != item.record_sha256:
            raise IntegrityFailure()
        previous = item.record_sha256


def assert_case_integrity(case: Case) -> None:
    """Verify local append chains and their bindings to current evidence metadata.

    This checks metadata consistency, not remote ciphertext-byte authenticity.
    """
    assert_chain(case.events)
    assert_chain(case.custody)
    if not case.events or not case.custody or case.events[-1].state != case.state:
        raise IntegrityFailure()
    if case.events[0].metadata_sha256 != digest({'seal': case.intake_seal_sha256,
            'source_occurred_at': case.intake_source_occurred_at,
            'verifier_id': case.intake_verifier_id}):
        raise IntegrityFailure()
    if case.custody[0].metadata_sha256 != digest({'manifest': case.intake_seal_sha256}):
        raise IntegrityFailure()
    receipts = [entry for entry in case.custody if entry.kind == 'RECEIVED']
    if len(receipts) != len(case.evidence):
        raise IntegrityFailure()
    event_index = {entry.entry_id: entry for entry in case.events}
    signatures: dict[tuple[str, str], str] = {}
    for ev in case.evidence.values():
        meta = {
            'evidence_id': ev.evidence_id, 'manifest': ev.manifest_sha256,
            'content': ev.content_sha256, 'verifier': ev.verifier_id,
            'scope': ev.access_scope, 'source_occurred_at': ev.source_occurred_at,
            'source_ref': ev.source_ref, 'source_revision': ev.source_revision,
            'source_event_id': ev.source_event_id, 'origin_ref': ev.origin_ref,
            'author_id': ev.author_id, 'counter_to': ev.counter_to,
            'corrects': ev.corrects, 'received_at': ev.received_at,
            'classification': ev.classification,
        }
        event_meta = {
            'content': ev.content_sha256, 'class': ev.classification,
            'evidence_id': ev.evidence_id,
        }
        signature = (digest(meta), digest(event_meta))
        if signature in signatures:
            raise IntegrityFailure()
        signatures[signature] = ev.evidence_id
    consumed: set[str] = set()
    for entry in receipts:
        if not entry.entry_id.endswith(':custody'):
            raise IntegrityFailure()
        base_id = entry.entry_id[:-len(':custody')]
        related = event_index.get(base_id)
        if related is None or related.kind != 'EVIDENCE_ADMITTED':
            raise IntegrityFailure()
        match = signatures.get((entry.metadata_sha256, related.metadata_sha256))
        if match is None or match in consumed:
            raise IntegrityFailure()
        consumed.add(match)
    if case.finding is not None:
        expected = digest(case.finding.__dict__)
        finding_entries = [e for e in case.events if e.kind == 'FINDING_PROPOSED']
        if not finding_entries:
            raise IntegrityFailure()
        current = case.finding
        meta = {'conclusion': current.conclusion, 'sufficiency': current.sufficiency,
                'policy_revision': current.policy_revision,
                'reason_digest': current.sealed_reason_sha256,
                'finding_digest': expected}
        if finding_entries[-1].metadata_sha256 != digest(meta):
            raise IntegrityFailure()


class InMemoryCaseStore:
    """Deep-copy-on-write CAS store for provisional tests only.

    P2 MUST replace this with an encrypted durable case store and independent
    access enforcement. No promise of secrecy or persistence is made here.
    """
    def __init__(self) -> None:
        self._lock = RLock()
        self._cases: dict[str, Case] = {}
        self._intakes: dict[str, str] = {}
        self._intake_hashes: dict[str, str] = {}

    def create(self, intake_id: str, fingerprint: str, new: Callable[[], Case]) -> tuple[Case, bool]:
        with self._lock:
            if intake_id in self._intakes:
                if self._intake_hashes[intake_id] != fingerprint:
                    raise Conflict()
                return deepcopy(self._cases[self._intakes[intake_id]]), False
            case = new()
            if case.case_id in self._cases:
                raise Conflict()
            assert_case_integrity(case)
            self._cases[case.case_id] = deepcopy(case)
            self._intakes[intake_id] = case.case_id
            self._intake_hashes[intake_id] = fingerprint
            return deepcopy(case), True

    def read(self, case_id: str) -> Case:
        with self._lock:
            if case_id not in self._cases:
                # Keep missing-case vs forbidden-case indistinguishable.
                raise Conflict()
            case = deepcopy(self._cases[case_id])
            assert_case_integrity(case)
            return case

    def update(self, case_id: str, fn: Callable[[Case], T]) -> T:
        with self._lock:
            if case_id not in self._cases:
                raise Conflict()
            case = deepcopy(self._cases[case_id])
            assert_chain(case.events)
            assert_chain(case.custody)
            result = fn(case)
            assert_case_integrity(case)
            self._cases[case_id] = case
            return result

    def aggregate(self) -> dict[str, int]:
        with self._lock:
            return {'total_cases': len(self._cases)}
