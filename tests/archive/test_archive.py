"""WG-L isolated state/authority/approval/evidence/witness fakes and faults."""
import gzip
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from nomad_studio.archive.api import build_component
from nomad_studio.archive.format import (ArchiveError, build_database, canonical,
                                         compress_database, digest, file_digest,
                                         inspect_archive, integrity_check, restore_evidence)
from nomad_studio.contracts.v1 import CommandEnvelope, QueryEnvelope, VerifiedIdentity

SRC = Path(__file__).resolve().parents[2] / "src/nomad_studio/state/schema_v1.sql"


class FakeStore:
    """Only test fixture; never used in source. SQLite-backed frozen port mock."""
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("CREATE TABLE event_audit (event_id TEXT PRIMARY KEY, kind TEXT NOT NULL)")
        self.conn.executescript(SRC.read_text())
        self.conn.commit()
        self.events = []
        self.fail_on_event_kind = None

    def fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params).rowcount

    def append_event(self, event):
        if event.kind == self.fail_on_event_kind:
            raise RuntimeError("injected StateTx journal commit failure")
        self.conn.execute("INSERT INTO event_audit VALUES (?,?)", (event.event_id, event.kind))
        self.events.append(event)
        return event.event_id

    def read(self, callback):
        return callback(self)

    def transact(self, callback, *, idempotency_key=None):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            result = callback(self)
            self.conn.commit()
            return result
        except BaseException:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()


class Authority:
    def __init__(self):
        self.epoch = 1
    def valid(self, identity):
        return identity.current_authority_epoch == self.epoch


class Approvals:
    def approved(self, *, job_id, approval_ref, principal_id, evidence_refs):
        return job_id.startswith("job") and approval_ref == "owner:approval-2026" and principal_id == "auditor-1"


class Snapshot:
    def __init__(self, db):
        self.db = db
        self.calls = 0
    def capture(self, destination, *, job_id):
        self.calls += 1
        with sqlite3.connect(self.db) as source, sqlite3.connect(destination) as dest:
            source.backup(dest)
        return {"sha256": file_digest(destination), "integrity_ok": True, "source_generation": "generation-1"}


class Evidence:
    def __init__(self):
        self.data = {("source", "github:repo@abc123", "abc123"): b"unaltered historical source bytes",
                     ("receipt", "drive:file-7", "revision-2"): b"immutable provider receipt"}
    def fetch(self, *, kind, reference, revision):
        return self.data[(kind, reference, revision)]
    def refs(self):
        return [{"kind": kind, "reference": reference, "revision": revision, "sha256": digest(data)}
                for (kind, reference, revision), data in self.data.items()]


class Witness:
    """Test trusted witness, binds external readback to a separately restored file."""
    def __init__(self):
        self.registered = {}
    def independently_restore(self, archive_id, archive_path, root):
        output = root / (archive_id + "-independently-restored.sqlite")
        result = inspect_archive(archive_path, restore_snapshot_to=output)
        assert file_digest(output) == result["snapshot_sha256"]
        with sqlite3.connect(output) as check:
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert check.execute("SELECT COUNT(*) FROM durable_event").fetchone()[0] == 2
            assert check.execute("SELECT value FROM durable_event WHERE n=2").fetchone()[0] == "event-B"
        self.registered[archive_id] = (result, file_digest(archive_path))
        return {"archive_id": archive_id, "witness_ref": "witness:receipt-1",
                "archive_sha256": result["archive_sha256"],
                "manifest_sha256": result["manifest_sha256"],
                "snapshot_sha256": result["snapshot_sha256"],
                "record_count": result["record_count"], "proof": "fake:trusted-witness-record"}
    def verify(self, receipt, *, identity, stored_sha256, archive_path):
        known = self.registered.get(receipt["archive_id"])
        return (known is not None and known[1] == stored_sha256 and
                known[0]["archive_sha256"] == receipt["archive_sha256"] and
                known[0]["manifest_sha256"] == receipt["manifest_sha256"] and
                known[0]["snapshot_sha256"] == receipt["snapshot_sha256"] and
                receipt["proof"] == "fake:trusted-witness-record" and
                identity.principal_id == "witness-2")


class ToyCipher:
    """Intentional fake cipher ONLY: tests injected cryptor file contract."""
    def encrypt_file(self, src, dest, *, archive_id):
        dest.write_bytes(b"FAKE" + bytes(c ^ 0xAA for c in src.read_bytes()))
    def decrypt_file(self, src, dest, *, archive_id):
        contents = src.read_bytes()
        if not contents.startswith(b"FAKE"):
            raise ValueError("corrupt fake ciphertext")
        dest.write_bytes(bytes(c ^ 0xAA for c in contents[4:]))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.live = self.root / "live.sqlite"
        with sqlite3.connect(self.live) as db:
            db.execute("CREATE TABLE durable_event (n INTEGER PRIMARY KEY, value TEXT)")
            db.executemany("INSERT INTO durable_event VALUES (?,?)", [(1, "event-A"), (2, "event-B")])
        self.store = FakeStore(self.root / "catalogue.sqlite")
        self.addCleanup(self.store.close)
        self.snapshot = Snapshot(self.live)
        self.evidence = Evidence()
        self.witness = Witness()
        self.authority = Authority()
        self.deps = {"archive_root": self.root / "archives", "state": self.store,
                     "authority": self.authority, "approvals": Approvals(),
                     "snapshot": self.snapshot, "evidence": self.evidence,
                     "witness_verifier": self.witness}
        self.component = build_component(self.deps)
        now = 9999999999999999
        self.auditor = VerifiedIdentity("auditor-1", "auditor-cred", "trusted-issuer", "gateway", frozenset({"archive:auditor", "archive:read"}), now, 1)
        self.witness_id = VerifiedIdentity("witness-2", "independent-cred", "separate-issuer", "gateway", frozenset({"archive:witness", "archive:read"}), now, 1)
        self.counter = 0

    def cmd(self, action, aid="archive-001", payload=None, identity=None, key=None, job="job-001"):
        self.counter += 1
        command = CommandEnvelope("NOMAD/CMD/1", f"req-{self.counter}", "archive", "archive." + action,
                                  key or f"key-{self.counter}", job_id=job,
                                  payload=payload if payload is not None else {"archive_id": aid})
        return self.component.execute(command, identity or self.auditor)

    def open(self, aid="archive-001", refs=None, job="job-001"):
        return self.cmd("open_job", aid, {"archive_id": aid, "approval_ref": "owner:approval-2026",
                                          "evidence_refs": self.evidence.refs() if refs is None else refs}, job=job)

    def compress(self, aid="archive-001", job="job-001"):
        self.assertEqual(self.open(aid, job=job).code, "OPEN")
        self.assertEqual(self.cmd("snapshot", aid, job=job).code, "SNAPSHOTTED")
        result = self.cmd("compress", aid, job=job)
        self.assertEqual(result.code, "COMPRESSED")
        return result

    def seal(self, aid="archive-001", job="job-001"):
        self.compress(aid, job)
        result = self.cmd("seal", aid, job=job)
        self.assertEqual(result.code, "SEALED_UNWITNESSED")
        return result

    def test_full_flow_witness_provenance_and_original_untouched(self):
        source_digest = file_digest(self.live)
        seal = self.seal()
        archive = self.root / "archives/archive-001.sqlite.gz"
        self.assertTrue(archive.is_file())
        check = inspect_archive(archive)
        self.assertEqual(check["record_count"], 2)
        self.assertEqual(seal.payload["status"], "NOT_VERIFIED")
        cat = self.component.query(QueryEnvelope("NOMAD/QUERY/1", "q1", "archive", "archive.catalogue",
                                                  payload={"archive_id": "archive-001", "reference": "github:repo@abc123"}), self.auditor)
        self.assertEqual(cat.payload["provenance"][0]["sha256"], digest(b"unaltered historical source bytes"))
        receipt = self.witness.independently_restore("archive-001", archive, self.root)
        witness = self.cmd("restore_proof", payload=receipt, identity=self.witness_id)
        self.assertEqual(witness.code, "WITNESSED")
        self.assertEqual(witness.payload["status"], "VERIFIED")
        self.assertEqual(file_digest(self.live), source_digest)
        with sqlite3.connect(self.live) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM durable_event").fetchone()[0], 2)
        self.assertEqual(len(self.store.events), 5)
        self.assertEqual(self.store.fetch_all("SELECT state FROM archive_jobs")[0]["state"], "WITNESSED")
        self.assertFalse(any("delete" in event.kind.lower() for event in self.store.events))

    def test_no_self_attestation_or_unverified_pass(self):
        self.seal()
        fake = {"archive_id": "archive-001", "witness_ref": "witness:receipt-1", "archive_sha256": self.store.fetch_one("SELECT archive_sha256 FROM archive_jobs")["archive_sha256"],
                "manifest_sha256": self.store.fetch_one("SELECT manifest_sha256 FROM archive_jobs")["manifest_sha256"],
                "snapshot_sha256": self.store.fetch_one("SELECT snapshot_sha256 FROM archive_jobs")["snapshot_sha256"], "record_count": 2, "proof": "self-asserted"}
        self.assertEqual(self.cmd("restore_proof", payload=fake).code, "INVALID_SCOPE")
        self.assertEqual(self.cmd("restore_proof", payload=fake, identity=self.witness_id).code, "IDENTITY_UNVERIFIED")
        self.assertEqual(self.store.fetch_one("SELECT state FROM archive_jobs")["state"], "SEALED_UNWITNESSED")
        self.component.deps = {**self.component.deps, "witness_verifier": None}
        self.assertEqual(self.cmd("restore_proof", payload=fake, identity=self.witness_id).code, "NOT_CONFIGURED")

    def test_corrupt_and_truncated_gzip_rejected(self):
        self.seal()
        archive = self.root / "archives/archive-001.sqlite.gz"
        original = archive.read_bytes()
        archive.write_bytes(original[:len(original)//2])
        with self.assertRaises(ArchiveError):
            inspect_archive(archive)
        archive.write_bytes(original)
        altered = bytearray(original)
        altered[-13] ^= 0x33
        archive.write_bytes(altered)
        with self.assertRaises(ArchiveError):
            inspect_archive(archive)

    def test_shard_digest_corruption_with_valid_sqlite_database(self):
        self.seal()
        archive = self.root / "archives/archive-001.sqlite.gz"
        unpacked = self.root / "archive-unpacked.sqlite"
        unpacked.write_bytes(gzip.decompress(archive.read_bytes()))
        with sqlite3.connect(unpacked) as db:
            db.execute("UPDATE shards SET payload=? WHERE entry_id=2 AND ordinal=0", (b"tamper",))
        integrity_check(unpacked)
        archive.write_bytes(gzip.compress(unpacked.read_bytes(), mtime=0))
        with self.assertRaises(ArchiveError):
            inspect_archive(archive)

    def test_logical_manifest_deterministic_identical_sources(self):
        records = [{"kind": "receipt", "reference": "b", "revision": "v1", "data": b"one"},
                   {"kind": "source", "reference": "a", "revision": "v2", "data": b"two"}]
        a = self.root / "one.sqlite"
        b = self.root / "two.sqlite"
        man_a = build_database(self.live, records, a, archive_id="same-archive")
        man_b = build_database(self.live, list(reversed(records)), b, archive_id="same-archive")
        self.assertEqual(canonical(man_a), canonical(man_b))
        zip_a = self.root / "one.gz"
        zip_b = self.root / "two.gz"
        compress_database(a, zip_a)
        compress_database(b, zip_b)
        self.assertEqual(inspect_archive(zip_a)["manifest_sha256"], inspect_archive(zip_b)["manifest_sha256"])

    def test_compression_crash_no_final_or_verified_status_then_retry(self):
        self.open()
        self.cmd("snapshot")
        original = compress_database
        def interrupted(db, partial, **kwargs):
            return original(db, partial, fail_after_bytes=1)
        with patch("nomad_studio.archive.api.compress_database", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "injected compression"):
                self.cmd("compress")
        self.assertFalse((self.root / "archives/archive-001.sqlite.gz").exists())
        self.assertEqual(self.store.fetch_one("SELECT state FROM archive_jobs")["state"], "SNAPSHOTTED")
        self.assertEqual(self.cmd("compress").code, "COMPRESSED")
        self.assertEqual(self.cmd("seal").code, "SEALED_UNWITNESSED")

    def test_atomic_rename_recovery_after_journal_crash(self):
        self.compress()
        self.store.fail_on_event_kind = "ArchiveSealedUnwitnessed"
        with self.assertRaisesRegex(RuntimeError, "journal commit failure"):
            self.cmd("seal")
        self.assertTrue((self.root / "archives/archive-001.sqlite.gz").exists())
        self.assertEqual(self.store.fetch_one("SELECT state FROM archive_jobs")["state"], "COMPRESSED")
        self.store.fail_on_event_kind = None
        self.assertEqual(self.cmd("seal").code, "SEALED_UNWITNESSED")
        self.assertEqual(self.store.fetch_one("SELECT state FROM archive_jobs")["state"], "SEALED_UNWITNESSED")

    def test_snapshot_hash_mismatch_rejected(self):
        class Fraud:
            def capture(self, dest, *, job_id):
                dest.write_bytes(b"not sqlite")
                return {"sha256": digest(b"not sqlite"), "integrity_ok": True, "source_generation": "x"}
        self.open()
        self.component.deps = {**self.deps, "snapshot": Fraud()}
        self.assertEqual(self.cmd("snapshot").code, "INVALID_SCHEMA")
        self.assertEqual(self.store.fetch_one("SELECT state FROM archive_jobs")["state"], "OPEN")

    def test_epoch_revocation_during_evidence_fetch_blocks_sql_commit(self):
        self.open()
        self.cmd("snapshot")
        authority = self.authority
        upstream = self.evidence
        class RevokingEvidence:
            def fetch(self, *, kind, reference, revision):
                authority.epoch += 1
                return upstream.fetch(kind=kind, reference=reference, revision=revision)
        self.component.deps = {**self.component.deps, "evidence": RevokingEvidence()}
        self.assertEqual(self.cmd("compress").code, "REVOKED_GRANT")
        self.assertEqual(self.store.fetch_one("SELECT state FROM archive_jobs")["state"], "SNAPSHOTTED")
        self.assertEqual(len(self.store.events), 2)
        self.assertFalse((self.root / "archives/archive-001.sqlite.gz").exists())

    def test_mutated_historical_evidence_rejected(self):
        self.open()
        self.cmd("snapshot")
        self.evidence.data[("source", "github:repo@abc123", "abc123")] = b"mutated source"
        self.assertEqual(self.cmd("compress").code, "SOURCE_SHA_MISMATCH")
        self.assertEqual(self.store.fetch_one("SELECT state FROM archive_jobs")["state"], "SNAPSHOTTED")

    def test_authority_approval_epoch_and_invalid_schema(self):
        self.assertEqual(self.cmd("open_job", payload={"archive_id": "a", "approval_ref": "false:approval", "evidence_refs": []}).code, "OWNER_APPROVAL_REQUIRED")
        self.assertEqual(self.cmd("open_job", payload={"archive_id": "../escape", "approval_ref": "owner:approval-2026", "evidence_refs": []}).code, "INVALID_SCHEMA")
        self.assertEqual(self.cmd("open_job", payload={"archive_id": "good", "approval_ref": "owner:approval-2026", "evidence_refs": [], "delete": True}).code, "INVALID_SCHEMA")
        self.authority.epoch = 2
        self.assertEqual(self.open().code, "REVOKED_GRANT")
        self.assertEqual(len(self.store.events), 0)

    def test_idempotency_exact_replay_and_conflict(self):
        payload = {"archive_id": "archive-001", "approval_ref": "owner:approval-2026", "evidence_refs": []}
        cmd = CommandEnvelope("NOMAD/CMD/1", "fixed-req", "archive", "archive.open_job", "fixed-key", job_id="job-001", payload=payload)
        a = self.component.execute(cmd, self.auditor)
        b = self.component.execute(cmd, self.auditor)
        self.assertEqual(a, b)
        retried_request = CommandEnvelope("NOMAD/CMD/1", "new-req-id", "archive", "archive.open_job", "fixed-key", job_id="job-001", payload=payload)
        self.assertEqual(self.component.execute(retried_request, self.auditor), a)
        changed = CommandEnvelope("NOMAD/CMD/1", "fixed-req", "archive", "archive.open_job", "fixed-key", job_id="job-001", payload={**payload, "archive_id": "other"})
        self.assertEqual(self.component.execute(changed, self.auditor).code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(len(self.store.events), 1)

    def test_disabled_encryption_requires_cryptor_and_fake_port_roundtrip(self):
        dep = {**self.deps, "encrypted": True}
        self.component = build_component(dep)
        self.compress()
        self.assertEqual(self.cmd("seal").code, "NOT_CONFIGURED")
        self.assertEqual(self.store.fetch_one("SELECT state FROM archive_jobs")["state"], "COMPRESSED")
        self.component.deps = {**dep, "cryptor": ToyCipher()}
        self.assertEqual(self.cmd("seal").code, "SEALED_UNWITNESSED")
        self.assertTrue((self.root / "archives/archive-001.sqlite.gz.enc").exists())
        cat = self.component.query(QueryEnvelope("NOMAD/QUERY/1", "q", "archive", "archive.catalogue",
                                                  payload={"archive_id": "archive-001", "reference": "drive:file-7"}), self.auditor)
        self.assertEqual(len(cat.payload["provenance"]), 1)

    def test_evidence_independent_offline_restore_and_no_overwrite(self):
        self.seal()
        archive = self.root / "archives/archive-001.sqlite.gz"
        output = self.root / "offline-evidence"
        result = restore_evidence(archive, output, kind="receipt", reference="drive:file-7", revision="revision-2")
        self.assertEqual(Path(result["path"]).read_bytes(), b"immutable provider receipt")
        self.assertEqual(result["sha256"], digest(b"immutable provider receipt"))
        with self.assertRaisesRegex(ArchiveError, "overwrite"):
            restore_evidence(archive, output, kind="receipt", reference="drive:file-7", revision="revision-2")
        with self.assertRaisesRegex(ArchiveError, "not found"):
            restore_evidence(archive, output, kind="source", reference="missing", revision="never")

    def test_invalid_archive_does_not_publish_partial_restored_snapshot(self):
        self.seal()
        archive = self.root / "archives/archive-001.sqlite.gz"
        original = archive.read_bytes()
        archive.write_bytes(original[:-17])
        target = self.root / "bad-restored.sqlite"
        with self.assertRaises(ArchiveError):
            inspect_archive(archive, restore_snapshot_to=target)
        self.assertFalse(target.exists())

    def test_hidden_shard_not_part_of_manifest_rejected(self):
        self.seal()
        archive = self.root / "archives/archive-001.sqlite.gz"
        unpacked = self.root / "hidden.sqlite"
        unpacked.write_bytes(gzip.decompress(archive.read_bytes()))
        with sqlite3.connect(unpacked) as db:
            db.execute("INSERT INTO shards(entry_id,ordinal,sha256,payload) VALUES (?,?,?,?)",
                       (12345, 0, digest(b"hidden"), b"hidden"))
        archive.write_bytes(gzip.compress(unpacked.read_bytes(), mtime=0))
        with self.assertRaises(ArchiveError):
            inspect_archive(archive)

    def test_distinct_actor_cannot_replay_idempotent_receipt(self):
        self.assertEqual(self.open().code, "OPEN")
        ref = self.store.fetch_all("SELECT key FROM archive_idempotency ORDER BY key")[0]["key"]
        replay = CommandEnvelope("NOMAD/CMD/1", "req-1", "archive", "archive.open_job", ref,
                                 job_id="job-001", payload={"archive_id": "archive-001", "approval_ref": "owner:approval-2026", "evidence_refs": self.evidence.refs()})
        altered_identity = VerifiedIdentity("auditor-2", "cred", "issuer", "gateway",
                                            frozenset({"archive:auditor"}), 9999999999999999, 1)
        self.assertEqual(self.component.execute(replay, altered_identity).code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(len(self.store.events), 1)

    def test_catalogue_requires_read_scope_and_is_bounded(self):
        self.open()
        result = self.component.query(QueryEnvelope("NOMAD/QUERY/1", "q", "archive", "archive.catalogue"), self.auditor)
        self.assertEqual(len(result.payload["archives"]), 1)
        result = self.component.query(QueryEnvelope("NOMAD/QUERY/1", "q", "archive", "archive.catalogue", limit=10000), self.auditor)
        self.assertEqual(result.code, "INVALID_SCHEMA")
        unsupported_cursor = self.component.query(QueryEnvelope("NOMAD/QUERY/1", "q", "archive", "archive.catalogue", cursor="page2"), self.auditor)
        self.assertEqual(unsupported_cursor.code, "NOT_SUPPORTED")
        bad = VerifiedIdentity("other", "c", "i", "gateway", frozenset({"archive:auditor"}), 9999999999999999, 1)
        self.assertEqual(self.component.query(QueryEnvelope("NOMAD/QUERY/1", "q", "archive", "archive.catalogue"), bad).code, "INVALID_SCOPE")


if __name__ == "__main__":
    unittest.main()
