"""WG-H deterministic, process-local fake of the V1.6 authority/DB/provider saga.

TEST-ONLY. Every authorization decision and durable effect is emulated; this
module is not a gateway, PostgreSQL driver, credential verifier or publisher.
No network calls, real credentials, model invocations or external writes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


class InjectedTimeout(RuntimeError):
    pass


class SimulatedCrash(RuntimeError):
    pass


@dataclass
class Counters:
    claims: int = 0
    claim_collisions: int = 0
    denied: int = 0
    duplicate_commands: int = 0
    duplicate_effects_prevented: int = 0
    provider_calls: int = 0
    provider_readbacks: int = 0
    provider_effects: int = 0
    recovered_effects: int = 0
    messages_processed: int = 0
    outbox_delivered: int = 0


@dataclass
class FakeProvider:
    """Immutable effect digest per publication id; fake GitHub/Drive CAS."""
    head: str = "head-0"
    drive_revision: int = 1
    effects: dict = field(default_factory=dict)
    counters: Counters = field(default_factory=Counters)

    def readback(self, key):
        self.counters.provider_readbacks += 1
        return copy.deepcopy(self.effects.get(key))

    def push(self, key, artifact, expected, kind="source", fault=None):
        self.counters.provider_calls += 1
        if fault == "timeout_before_effect":
            raise InjectedTimeout("before external effect")
        previous = self.effects.get(key)
        if previous is not None:
            return ("DUPLICATE" if previous["artifact"] == artifact else "IDEMPOTENCY_CONFLICT", previous)
        if kind == "source":
            if self.head != expected:
                return "REBASE_REQUIRED", None
            self.head = "head-" + artifact[:16]
            result = {"kind": kind, "artifact": artifact, "revision": self.head}
        elif kind == "drive":
            if self.drive_revision != expected:
                return "DOCUMENT_CONFLICT", None
            if fault == "partial_drive_write":
                # Staged but not committed: no revision, no canonical effect.
                return "RETRYABLE_BLOCKED", None
            self.drive_revision += 1
            result = {"kind": kind, "artifact": artifact, "revision": self.drive_revision}
        else:
            return "INVALID_SCOPE", None
        self.effects[key] = result
        self.counters.provider_effects += 1
        if fault == "timeout_after_effect":
            raise InjectedTimeout("external effect persisted, response lost")
        return "COMMITTED", copy.deepcopy(result)


class FakeJournal:
    """Locked in-memory reference state; not a substitute for ACID/PG tests."""

    def __init__(self, worker="worker-0", epoch=1, generation=0):
        self.lock = threading.RLock()
        self.worker = worker
        self.epoch = epoch
        self.generation = generation
        self.holder = None
        self.approved = True
        self.lease_valid = True
        self.policy_revision = "policy-v1"
        self.authority_revision = "authority-v1"
        self.claim_keys = {}
        self.commands = {}
        self.acks = set()
        self.publications = {}
        self.events = {}
        self.outbox = {}
        self.counter = 0
        self.stats = Counters()

    def claim(self, worker, key, epoch=1, approved=True, credential_verified=True,
              policy_revision="policy-v1"):
        with self.lock:
            self.stats.claims += 1
            payload = digest([worker, epoch, approved, credential_verified, policy_revision])
            if key in self.claim_keys:
                stored = self.claim_keys[key]
                if stored[0] != payload:
                    self.stats.denied += 1
                    return "IDEMPOTENCY_CONFLICT"
                self.stats.duplicate_commands += 1
                return "DUPLICATE"
            if not credential_verified:
                self.stats.denied += 1
                return "IDENTITY_UNVERIFIED"
            if not approved or not self.approved:
                self.stats.denied += 1
                return "OWNER_APPROVAL_REQUIRED"
            if policy_revision != self.policy_revision:
                self.stats.denied += 1
                return "POLICY_REVISION_MISMATCH"
            if epoch != self.epoch:
                self.stats.denied += 1
                return "STALE_ASSIGNMENT"
            if worker != self.worker:
                self.stats.denied += 1
                return "INVALID_SCOPE"
            if self.holder is not None:
                self.stats.claim_collisions += 1
                return "LEASE_HELD"
            self.generation += 1
            self.holder = worker
            self.claim_keys[key] = (payload, self.generation)
            return "COMMITTED"

    def authorize(self, worker, epoch, generation, *, verified=True, approved=True,
                  policy_revision="policy-v1", qa_reviewer="reviewer-1",
                  principal="worker-0", source_sha_match=True, path="tests/v16/system/test.py"):
        if not verified:
            return "IDENTITY_UNVERIFIED"
        if not approved or not self.approved:
            return "OWNER_APPROVAL_REQUIRED"
        if policy_revision != self.policy_revision:
            return "POLICY_REVISION_MISMATCH"
        if not self.lease_valid:
            return "EXPIRED_GRANT"
        if epoch != self.epoch:
            return "STALE_ASSIGNMENT"
        if generation != self.generation or self.holder != worker:
            return "STALE_LEASE"
        if worker != self.worker:
            return "INVALID_SCOPE"
        if qa_reviewer == principal:
            return "SELF_REVIEW"
        if not source_sha_match:
            return "REBASE_REQUIRED"
        # No link-following in fake: these are synthetic path/type metadata.
        if (not isinstance(path, str) or not path.startswith("tests/v16/")
                or any(p in ("", ".", "..") for p in path.split("/"))
                or "\\" in path or path.startswith("/")):
            return "INVALID_SCOPE"
        return "ALLOW"

    def reserve(self, key, artifact, worker, epoch, generation, **authorization):
        with self.lock:
            outcome = self.authorize(worker, epoch, generation, **authorization)
            if outcome != "ALLOW":
                self.stats.denied += 1
                return outcome
            if key in self.publications:
                if self.publications[key]["artifact"] != artifact:
                    self.stats.denied += 1
                    return "IDEMPOTENCY_CONFLICT"
                self.stats.duplicate_effects_prevented += 1
                return "DUPLICATE"
            self.publications[key] = {"artifact": artifact, "state": "RESERVED", "observed": None}
            return "COMMITTED"

    def reconcile(self, key, observed):
        with self.lock:
            if key not in self.publications or observed is None:
                return "RETRYABLE_BLOCKED"
            if observed["artifact"] != self.publications[key]["artifact"]:
                return "IDEMPOTENCY_CONFLICT"
            self.publications[key]["state"] = "VERIFIED"
            self.publications[key]["observed"] = copy.deepcopy(observed)
            return "VERIFIED"

    def ack(self, event_id, recipient, epoch, effect):
        with self.lock:
            if epoch != self.epoch:
                self.stats.denied += 1
                return "STALE_ASSIGNMENT"
            key = (event_id, recipient, epoch, effect)
            if key in self.acks:
                self.stats.duplicate_effects_prevented += 1
                return "DUPLICATE"
            self.acks.add(key)
            self.stats.messages_processed += 1
            return "COMMITTED"

    def accept_event(self, event_id, payload, kind="OBSERVATION"):
        with self.lock:
            hashed = digest(payload)
            if event_id in self.events:
                if self.events[event_id][0] != hashed:
                    return "IDEMPOTENCY_CONFLICT"
                return "DUPLICATE"
            if kind in ("LAUNCH_CHAT", "MODEL_RUN", "SCHEDULE_AI"):
                return "INVALID_SCOPE"
            self.counter += 1
            self.events[event_id] = (hashed, self.counter)
            return "COMMITTED"

    def enqueue(self, event_id, destination, kind, payload):
        with self.lock:
            if kind in ("LAUNCH_CHAT", "MODEL_RUN", "SCHEDULE_AI"):
                return "INVALID_SCOPE"
            key = (event_id, destination, kind)
            payload_hash = digest(payload)
            if key in self.outbox:
                return ("DUPLICATE" if self.outbox[key][0] == payload_hash
                        else "IDEMPOTENCY_CONFLICT")
            self.outbox[key] = [payload_hash, False]
            return "COMMITTED"

    def deliver(self, event_id, destination, kind):
        with self.lock:
            key = (event_id, destination, kind)
            if key not in self.outbox:
                return "RETRYABLE_BLOCKED"
            if self.outbox[key][1]:
                return "DUPLICATE"
            self.outbox[key][1] = True
            self.stats.outbox_delivered += 1
            return "COMMITTED"

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({k: getattr(self, k) for k in (
                "worker", "epoch", "generation", "holder", "approved", "lease_valid", "policy_revision",
                "authority_revision", "claim_keys", "commands", "acks", "publications",
                "events", "outbox", "counter", "stats")})

    @classmethod
    def restore(cls, snap):
        obj = cls()
        for k, v in copy.deepcopy(snap).items():
            setattr(obj, k, v)
        return obj


def publish(journal, provider, key, artifact, *, worker="worker-0", epoch=1,
            generation=1, kind="source", expected="head-0", fault=None, crash_at=None,
            **authorization):
    """Synthetic reserve -> provider -> verified readback -> reconcile saga."""
    status = journal.reserve(key, artifact, worker, epoch, generation, **authorization)
    if status not in ("COMMITTED", "DUPLICATE"):
        return status
    if crash_at == "after_reservation":
        raise SimulatedCrash("after durable reservation")
    current = journal.publications[key]
    if current["state"] == "VERIFIED":
        return "VERIFIED"
    observed = provider.readback(key)
    if observed is not None:
        if observed["artifact"] != artifact:
            return "IDEMPOTENCY_CONFLICT"
        journal.stats.recovered_effects += 1
        return journal.reconcile(key, observed)
    try:
        outcome, _ = provider.push(key, artifact, expected, kind=kind, fault=fault)
    except InjectedTimeout:
        return "RETRYABLE_BLOCKED"
    if outcome not in ("COMMITTED", "DUPLICATE"):
        return outcome
    if crash_at == "after_provider_effect":
        raise SimulatedCrash("after external effect, before reconciliation")
    return journal.reconcile(key, provider.readback(key))


def serialize_checkpoint(db):
    """JSON checkpoint fixture; independent from mutable in-memory references."""
    state = db.snapshot()
    state["acks"] = [list(k) for k in sorted(state["acks"])]
    state["outbox"] = [{"key": list(k), "value": v}
                       for k, v in sorted(state["outbox"].items())]
    state["stats"] = asdict(state["stats"])
    return json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")


def restore_checkpoint(payload, expected_sha256):
    """Exercise integrity-checked fake snapshot restoration, not PostgreSQL PITR."""
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("CHECKPOINT_INTEGRITY_MISMATCH")
    state = json.loads(payload.decode("utf-8"))
    state["acks"] = set(tuple(k) for k in state["acks"])
    state["outbox"] = {tuple(item["key"]): item["value"] for item in state["outbox"]}
    state["stats"] = Counters(**state["stats"])
    return FakeJournal.restore(state)
