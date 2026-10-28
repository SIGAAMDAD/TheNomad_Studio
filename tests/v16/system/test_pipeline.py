"""Synthetic transactional and provider fault tests; zero external connectivity."""
import hashlib
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import unittest
from fake_pipeline import FakeJournal, FakeProvider, SimulatedCrash, publish, serialize_checkpoint, restore_checkpoint


class SyntheticSagaTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeJournal()
        self.assertEqual(self.db.claim("worker-0", "claim-0"), "COMMITTED")
        self.provider = FakeProvider()

    def test_one_claim_winner_among_100_simultaneous_attempts(self):
        db = FakeJournal()
        with ThreadPoolExecutor(max_workers=25) as pool:
            outcomes = list(pool.map(lambda i: db.claim("worker-0", f"claim-{i}"), range(100)))
        self.assertEqual(outcomes.count("COMMITTED"), 1)
        self.assertEqual(outcomes.count("LEASE_HELD"), 99)
        self.assertEqual(db.generation, 1)
        self.assertEqual(db.stats.claim_collisions, 99)

    def test_claim_idempotency_payload_and_revocation(self):
        self.assertEqual(self.db.claim("worker-0", "claim-0"), "DUPLICATE")
        self.assertEqual(self.db.claim("worker-0", "claim-0", epoch=2), "IDEMPOTENCY_CONFLICT")
        self.db.approved = False
        self.assertEqual(publish(self.db, self.provider, "pub", "a" * 64), "OWNER_APPROVAL_REQUIRED")
        self.assertFalse(self.provider.effects)

    def test_expiry_epoch_lease_fences_and_role_forgery(self):
        for kwargs, expected in [
            ({"epoch": 0}, "STALE_ASSIGNMENT"),
            ({"generation": 0}, "STALE_LEASE"),
            ({"worker": "forged-head"}, "STALE_LEASE"),
            ({"verified": False}, "IDENTITY_UNVERIFIED"),
            ({"approved": False}, "OWNER_APPROVAL_REQUIRED"),
            ({"policy_revision": "policy-v0"}, "POLICY_REVISION_MISMATCH"),
            ({"qa_reviewer": "worker-0"}, "SELF_REVIEW"),
            ({"path": "../.github/workflows/unsafe.yml"}, "INVALID_SCOPE"),
        ]:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(publish(self.db, self.provider, "pub", "a" * 64, **kwargs), expected)
        self.assertEqual(self.provider.counters.provider_calls, 0)

    def test_external_timeout_after_success_no_second_write(self):
        artifact = "a" * 64
        self.assertEqual(publish(self.db, self.provider, "p", artifact, fault="timeout_after_effect"), "RETRYABLE_BLOCKED")
        self.assertEqual(self.provider.counters.provider_effects, 1)
        self.assertEqual(publish(self.db, self.provider, "p", artifact), "VERIFIED")
        self.assertEqual(self.provider.counters.provider_calls, 1)
        self.assertEqual(self.db.stats.recovered_effects, 1)

    def test_timeout_before_effect_retries_once(self):
        self.assertEqual(publish(self.db, self.provider, "p", "a" * 64, fault="timeout_before_effect"), "RETRYABLE_BLOCKED")
        self.assertEqual(self.provider.counters.provider_effects, 0)
        self.assertEqual(publish(self.db, self.provider, "p", "a" * 64), "VERIFIED")
        self.assertEqual(self.provider.counters.provider_effects, 1)

    def test_expired_lease_blocks_effect(self):
        self.db.lease_valid = False
        self.assertEqual(publish(self.db, self.provider, "p", "a" * 64), "EXPIRED_GRANT")
        self.assertFalse(self.provider.effects)

    def test_disk_snapshot_roundtrip_and_integrity(self):
        self.assertEqual(self.db.accept_event("e", {"digest": "a"}), "COMMITTED")
        self.assertEqual(self.db.enqueue("e", "email", "NOTICE", {"a": 1}), "COMMITTED")
        self.assertEqual(self.db.ack("e", "worker-0", 1, "inbox"), "COMMITTED")
        data = serialize_checkpoint(self.db)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pg-restore-mock.json"
            path.write_bytes(data)
            self.db = restore_checkpoint(path.read_bytes(), hashlib.sha256(data).hexdigest())
        self.assertEqual(self.db.accept_event("e", {"digest": "a"}), "DUPLICATE")
        self.assertEqual(self.db.deliver("e", "email", "NOTICE"), "COMMITTED")
        self.assertEqual(self.db.ack("e", "worker-0", 1, "inbox"), "DUPLICATE")
        with self.assertRaisesRegex(ValueError, "CHECKPOINT_INTEGRITY_MISMATCH"):
            restore_checkpoint(data + b"!", hashlib.sha256(data).hexdigest())

    def test_crash_after_reservation_restore(self):
        with self.assertRaises(SimulatedCrash):
            publish(self.db, self.provider, "p", "a" * 64, crash_at="after_reservation")
        self.db = FakeJournal.restore(self.db.snapshot())
        self.assertEqual(publish(self.db, self.provider, "p", "a" * 64), "VERIFIED")
        self.assertEqual(self.provider.counters.provider_effects, 1)

    def test_crash_after_provider_write_restore(self):
        with self.assertRaises(SimulatedCrash):
            publish(self.db, self.provider, "p", "a" * 64, crash_at="after_provider_effect")
        self.db = FakeJournal.restore(self.db.snapshot())
        self.assertEqual(publish(self.db, self.provider, "p", "a" * 64), "VERIFIED")
        self.assertEqual(self.provider.counters.provider_calls, 1)
        self.assertEqual(self.db.publications["p"]["state"], "VERIFIED")

    def test_changed_artifact_idempotency_conflict(self):
        self.assertEqual(publish(self.db, self.provider, "p", "a" * 64), "VERIFIED")
        self.assertEqual(publish(self.db, self.provider, "p", "b" * 64), "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.provider.counters.provider_effects, 1)

    def test_drive_partial_write_recovery_and_revision_conflict(self):
        self.assertEqual(publish(self.db, self.provider, "drive-a", "a" * 64,
                                 kind="drive", expected=1, fault="partial_drive_write"), "RETRYABLE_BLOCKED")
        self.assertEqual(self.provider.drive_revision, 1)
        self.assertEqual(publish(self.db, self.provider, "drive-a", "a" * 64,
                                 kind="drive", expected=1), "VERIFIED")
        self.assertEqual(self.provider.drive_revision, 2)
        self.assertEqual(publish(self.db, self.provider, "drive-b", "b" * 64,
                                 kind="drive", expected=1), "DOCUMENT_CONFLICT")
        self.assertEqual(self.provider.drive_revision, 2)

    def test_source_merge_conflict_is_not_overwritten(self):
        self.provider.head = "other-commit"
        self.assertEqual(publish(self.db, self.provider, "p", "a" * 64), "REBASE_REQUIRED")
        self.assertEqual(self.provider.head, "other-commit")
        self.assertEqual(self.provider.counters.provider_effects, 0)

    def test_out_of_order_event_ack_and_duplicate_webhooks(self):
        self.assertEqual(self.db.ack("evt-7", "worker-0", 1, "inbox"), "COMMITTED")
        self.assertEqual(self.db.accept_event("evt-7", {"kind": "published"}), "COMMITTED")
        self.assertEqual(self.db.accept_event("evt-7", {"kind": "published"}), "DUPLICATE")
        self.assertEqual(self.db.accept_event("evt-7", {"kind": "forged"}), "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.db.ack("evt-7", "worker-0", 1, "inbox"), "DUPLICATE")
        self.assertEqual(self.db.ack("evt-7", "worker-0", 0, "inbox"), "STALE_ASSIGNMENT")
        self.assertEqual(len(self.db.acks), 1)

    def test_restore_outbox_replay_once(self):
        self.assertEqual(self.db.enqueue("e", "email", "NOTICE", {"ok": True}), "COMMITTED")
        self.db = FakeJournal.restore(self.db.snapshot())
        self.assertEqual(self.db.enqueue("e", "email", "NOTICE", {"ok": True}), "DUPLICATE")
        self.assertEqual(self.db.deliver("e", "email", "NOTICE"), "COMMITTED")
        self.db = FakeJournal.restore(self.db.snapshot())
        self.assertEqual(self.db.deliver("e", "email", "NOTICE"), "DUPLICATE")
        self.assertEqual(self.db.stats.outbox_delivered, 1)

    def test_never_schedule_ai_from_event_or_outbox(self):
        for kind in ("LAUNCH_CHAT", "MODEL_RUN", "SCHEDULE_AI"):
            self.assertEqual(self.db.accept_event(kind, {}, kind), "INVALID_SCOPE")
            self.assertEqual(self.db.enqueue(kind, "endpoint", kind, {}), "INVALID_SCOPE")
        self.assertFalse(self.db.outbox)

    def test_new_lease_invalidates_old_authorization(self):
        old = self.db.generation
        self.db.epoch += 1
        self.db.generation += 1
        self.db.holder = "worker-1"
        self.db.worker = "worker-1"
        self.assertEqual(publish(self.db, self.provider, "p", "a" * 64,
                                 worker="worker-0", epoch=1, generation=old), "STALE_ASSIGNMENT")
        self.assertEqual(self.provider.counters.provider_effects, 0)


if __name__ == "__main__":
    unittest.main()
