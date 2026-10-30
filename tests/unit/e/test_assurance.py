"""WG-E positive/negative tests against the actual frozen SQL triggers.

Set R5_SCHEMA_PATH to CONTRACTS/initial_schema.sql from the exact R5 plan.
Tests deliberately use a test-only sqlite3 transaction adapter; WG-C's production
APSW/scoped writer, WG-G readback and host validators require integration tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from studio.assurance import AssuranceError, AssuranceService, ReviewerAssignment, ValidationResult


def digest(b):
    return hashlib.sha256(b).hexdigest()


def canon(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":")).encode()


class Vault:
    def __init__(self):
        self.content = {}

    def get(self, *, project_id, digest):
        try:
            return self.content[(project_id, digest)]
        except KeyError as exc:
            raise AssuranceError("missing", "project-scoped verified bytes unavailable") from exc

    def put_verified(self, *, project_id, content):
        h = digest(content)
        self.content[project_id, h] = content
        return h


class Directory:
    def __init__(self):
        self.selected = ReviewerAssignment("reviewer", None)

    def choose(self, **kwargs):
        return self.selected


class Validators:
    def __init__(self):
        self.status = "pass"
        self.calls = 0

    def run(self, *, project_id, kind, validator_digest, acceptance_digest, original):
        self.calls += 1
        if self.status == "unavailable":
            return None
        assert kind == "json"
        assert validator_digest == digest(b"installed-json-validator")
        assert acceptance_digest == digest(b"acceptance-profile")
        try:
            data = json.loads(original)
            actual = "pass" if data == {"valid": True} else "fail"
        except Exception:
            actual = "fail"
        if self.status == "fail":
            actual = "fail"
        return ValidationResult(actual, canon({"test": "json-validator", "sha256": digest(original), "status": actual}))


class Tx:
    def __init__(self, conn, events):
        self.conn = conn
        self.events = events

    def one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        self.conn.execute(sql, params)

    def event(self, *, event_type, subject_id, payload_digest):
        self.events.append((event_type, subject_id, payload_digest))


class Store:
    """Deterministic test adapter, NOT a WG-C production writer replacement."""

    def __init__(self, conn):
        self.conn = conn
        self.cache = {}
        self.events = []

    def run(self, *, project_id, domain, operation, principal_id, request_id, request_digest, command):
        key = (project_id, domain, operation, principal_id, request_id)
        old = self.cache.get(key)
        if old is not None:
            if old[0] != request_digest:
                raise AssuranceError("conflict", "idempotency key reused with different body")
            return old[1]
        n = len(self.events)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            result = command(Tx(self.conn, self.events))
            self.conn.commit()
            self.cache[key] = (request_digest, result)
            return result
        except Exception:
            self.conn.rollback()
            del self.events[n:]
            raise

    def read(self, *, project_id, query):
        return query(Tx(self.conn, self.events))


SCHEMA = Path(os.environ.get("R5_SCHEMA_PATH", ROOT / "CONTRACTS" / "initial_schema.sql"))
if not SCHEMA.exists():
    # A standalone WG-E handoff is deliberately changed-files-only; the owner
    # packet's SQL is supplied as R5_SCHEMA_PATH rather than duplicated/modified.
    raise RuntimeError("Provide R5_SCHEMA_PATH pointing to frozen CONTRACTS/initial_schema.sql")


class AssuranceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA.read_text())
        self.conn.execute("PRAGMA foreign_keys=OFF")  # test fixture creates Canon prerequisites without A's project bootstrap
        self.vault = Vault()
        self.directory = Directory()
        self.validators = Validators()
        self.store = Store(self.conn)
        self.svc = AssuranceService(self.store, self.vault, self.directory, self.validators)
        self.author = self.actor("author", "P")
        self.reviewer = self.actor("reviewer", "P")
        self.owner = self.actor("owner", "P")
        self.seed()

    def tearDown(self):
        self.conn.close()

    @staticmethod
    def actor(principal, project):
        return {"principal_id": principal, "issuer": "test-trusted-auth", "allowed_project_ids": [project],
                "trusted_proof_ref": "auth:unit-test"}

    def sql(self, query, params=()):
        self.conn.execute(query, params)

    def seed(self):
        for principal, kind in [("author", "worker"), ("owner", "human"), ("reviewer", "human"),
                                 ("outsider", "human"), ("descendant", "worker"), ("independent", "worker")]:
            self.sql("INSERT INTO principals(principal_id,auth_issuer,auth_subject,kind) VALUES(?,?,?,?)",
                     (principal, "test", principal, kind))
        for p in ("P", "Q"):
            self.sql("INSERT INTO projects(project_id,name,charter_digest,state) VALUES(?,?,?,'active')", (p, p, digest(p.encode())))
        for p, who in [("P", "author"), ("P", "reviewer"), ("P", "owner"), ("P", "descendant"),
                       ("P", "independent"), ("Q", "owner"), ("Q", "outsider")]:
            self.sql("INSERT INTO project_grants(project_id,principal_id,grant_code) VALUES(?,?,?)", (p, who, "owner" if who == "owner" else "member"))
        # Frozen schema defines FKs to published bibles; WG-A's bootstrap is not
        # present in this independent E test fixture. All E-owned SQL triggers run.
        self.sql("""INSERT INTO canon_snapshots(project_id,snapshot_id,design_bible_id,design_edition_id,
                  technical_bible_id,technical_edition_id,policy_digest,manifest_digest)
                  VALUES('P','canon','design','edition','technical','edition','policy','manifest')""")
        self.sql("INSERT INTO cabals(project_id,cabal_id,name,charter_digest,state) VALUES('P','cabal','C','charter','active')")
        self.sql("INSERT INTO features(project_id,feature_id,brief_digest,snapshot_id,state) VALUES('P','feature','brief','canon','running')")
        self.acceptance = digest(b"acceptance-profile")
        self.sql("INSERT INTO tasks(project_id,task_id,feature_id,cabal_id,snapshot_id,acceptance_digest,state) VALUES('P','task','feature','cabal','canon',?,'submitted')", (self.acceptance,))
        self.sql("INSERT INTO leases(project_id,task_id,generation,worker_id,expires_at,status) VALUES('P','task',1,'worker','2030','active')")
        self.sql("INSERT INTO package_revisions(package_digest,package_id,package_version,manifest_digest,kind,state) VALUES('pkg','pkg','1','manifest','other','installed')")
        for session, principal in [("author-session", "author"), ("descendant-session", "descendant"),
                                   ("independent-session", "independent")]:
            self.sql("""INSERT INTO sessions(project_id,session_id,author_principal_id,task_id,lease_generation,package_digest,snapshot_id,state)
                      VALUES('P',?,?,'task',1,'pkg','canon','prepared')""", (session, principal))
        for session, root in [("author-session", "author-session"), ("descendant-session", "author-session"),
                              ("independent-session", "independent-session")]:
            self.sql("INSERT INTO session_lineages(project_id,session_id,root_session_id,lineage_digest) VALUES('P',?,?,?)",
                     (session, root, digest((session+root).encode())))
        self.sql("INSERT INTO project_repositories(project_id,repo_id,provider_key,remote_ref) VALUES('P','repo','github','main')")
        self.sql("INSERT INTO source_revisions(project_id,revision_id,repo_id,commit_sha,tree_digest,state) VALUES('P','rev','repo','abc123','tree123','staged')")
        self.source_bytes = b'{"valid":true}'
        self.put_artifact("artifact", self.source_bytes)
        self.sql("""INSERT INTO submissions(project_id,submission_id,session_id,task_id,lease_generation,source_revision_id,manifest_digest,state)
                  VALUES('P','sub','author-session','task',1,'rev','sub-manifest','staged')""")
        self.sql("UPDATE submissions SET state='submitted' WHERE project_id='P' AND submission_id='sub'")
        self.sql("INSERT INTO submission_artifacts(project_id,submission_id,artifact_id) VALUES('P','sub','artifact')")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def put_artifact(self, artifact_id, contents):
        content_digest = self.vault.put_verified(project_id="P", content=contents)
        manifest = {"project_id": "P", "artifact_id": artifact_id, "kind": "json", "content_digest": content_digest,
                    "manifest_digest": "declared-self-digest", "producer_session_id": "author-session",
                    "source_revision_id": "rev", "canon_snapshot_id": "canon",
                    "validator_digest": digest(b"installed-json-validator"), "storage_key": "blob-key"}
        manifest_digest = self.vault.put_verified(project_id="P", content=canon(manifest))
        for d, purpose in [(content_digest, "artifact-content"), (manifest_digest, "artifact-manifest")]:
            self.sql("INSERT OR IGNORE INTO blobs(content_digest,byte_size,store_key,durability) VALUES(?,?,?,'verified')", (d, len(self.vault.get(project_id="P", digest=d)), d))
            self.sql("INSERT OR IGNORE INTO project_blobs(project_id,content_digest,purpose) VALUES('P',?,?)", (d, purpose))
        self.sql("""INSERT INTO artifacts(project_id,artifact_id,kind,content_digest,manifest_digest,producer_session_id,source_revision_id,snapshot_id)
                  VALUES('P',?,'json',?,?,'author-session','rev','canon')""", (artifact_id, content_digest, manifest_digest))

    def request(self, request_id="r1"):
        return self.svc.requestReview(self.author, "P", {"request_id": request_id, "submission_id": "sub", "acceptance_digest": self.acceptance})["review_id"]

    def verdict(self, review_id, principal=None, **updates):
        req = {"request_id": "v1", "verdict": "pass", "evidence_digests": [], "source_revision_id": "rev"}
        req.update(updates)
        return self.svc.submitReview(principal or self.reviewer, "P", review_id, req)

    def rejected(self, code, callback):
        with self.assertRaises(AssuranceError) as context:
            callback()
        self.assertEqual(context.exception.code, code)

    def test_01_true_original_byte_pass_and_sql_immutability(self):
        rid = self.request()
        self.assertEqual(self.verdict(rid)["verdict"], "pass")
        receipt = self.conn.execute("SELECT evidence_digest FROM qa_receipts WHERE review_id=?", (rid,)).fetchone()
        self.assertTrue(self.vault.get(project_id="P", digest=receipt[0]))
        self.assertEqual(len(self.store.events), 2)
        self.assertEqual(self.svc.listReviews(self.author, "P")["reviews"][0]["verdict"], "pass")
        self.assertTrue(self.svc.requireExactIndependentPass(self.owner, "P", "sub", "rev"))
        self.sql("UPDATE submissions SET state='qa_passed' WHERE project_id='P' AND submission_id='sub'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql("DELETE FROM qa_receipts WHERE review_id=?", (rid,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql("DELETE FROM qa_reviews WHERE review_id=?", (rid,))

    def test_02_self_review_rejected(self):
        self.directory.selected = ReviewerAssignment("author", None)
        self.rejected("forbidden", self.request)
        self.assertFalse(self.conn.execute("SELECT 1 FROM qa_reviews").fetchone())

    def test_03_same_root_descendant_rejected(self):
        self.directory.selected = ReviewerAssignment("descendant", "descendant-session")
        self.rejected("forbidden", self.request)

    def test_04_separate_worker_lineage_allowed(self):
        self.directory.selected = ReviewerAssignment("independent", "independent-session")
        rid = self.request()
        self.assertEqual(self.verdict(rid, principal=self.actor("independent", "P"))["verdict"], "pass")

    def test_05_unassigned_reviewer_cannot_submit(self):
        rid = self.request()
        self.rejected("forbidden", lambda: self.verdict(rid, self.owner))

    def test_06_unauthorized_cross_project_views(self):
        self.request()
        self.rejected("forbidden", lambda: self.svc.listReviews(self.actor("outsider", "Q"), "P"))
        self.rejected("forbidden", lambda: self.svc.listOversightCases(self.actor("outsider", "Q"), "P"))
        self.assertEqual(self.svc.listReviews(self.actor("owner", "Q"), "Q"), {"reviews": []})

    def test_07_mutated_attachment_set_rejected(self):
        rid = self.request()
        self.put_artifact("extra", self.source_bytes)
        self.sql("INSERT INTO submission_artifacts(project_id,submission_id,artifact_id) VALUES('P','sub','extra')")
        self.rejected("conflict", lambda: self.verdict(rid))

    def test_08_tampered_original_bytes_rejected(self):
        rid = self.request()
        d = digest(self.source_bytes)
        self.vault.content["P", d] = b'tampered'
        self.rejected("conflict", lambda: self.verdict(rid))

    def test_09_validator_missing_incomplete_not_pass(self):
        self.validators.status = "unavailable"
        rid = self.request()
        self.assertEqual(self.verdict(rid)["verdict"], "incomplete")
        self.rejected("qa_failed", lambda: self.svc.requireExactIndependentPass(self.owner, "P", "sub", "rev"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql("UPDATE submissions SET state='qa_passed' WHERE project_id='P' AND submission_id='sub'")

    def test_10_validator_failure_never_pass(self):
        self.validators.status = "fail"
        rid = self.request()
        self.assertEqual(self.verdict(rid)["verdict"], "fail")

    def test_11_wrong_evidence_claim_rejected(self):
        rid = self.request()
        self.rejected("missing", lambda: self.verdict(rid, evidence_digests=["f" * 64]))
        self.assertEqual(self.conn.execute("SELECT verdict FROM qa_reviews WHERE review_id=?", (rid,)).fetchone()[0], "pending")

    def test_12_idempotent_replay_and_body_mismatch(self):
        a = self.request()
        self.assertEqual(a, self.request())
        self.assertEqual(1, len(self.store.events))
        self.rejected("conflict", lambda: self.svc.requestReview(self.author, "P", {"request_id": "r1", "submission_id": "different", "acceptance_digest": self.acceptance}))

    def test_13_source_change_fences_candidate(self):
        rid = self.request()
        self.verdict(rid)
        self.rejected("stale_revision", lambda: self.svc.requireExactIndependentPass(self.owner, "P", "sub", "merged-new-revision"))
        self.rejected("conflict", lambda: self.verdict(rid, request_id="v2", source_revision_id="merged-new-revision"))

    def test_14_oversight_evidence_auditor_and_project_scope(self):
        evidence = self.vault.put_verified(project_id="P", content=b'actual investigation evidence')
        req = {"request_id": "o1", "subject_id": "sub", "evidence_digest": evidence}
        opened = self.svc.openOversightCase(self.owner, "P", req)
        self.assertEqual(self.svc.openOversightCase(self.owner, "P", req), opened)
        case_id = opened["case_id"]
        self.assertEqual(self.svc.listOversightCases(self.owner, "P")["cases"][0]["state"], "open")
        resolution = {"project_id": "P", "case_id": case_id, "subject_id": "sub", "initial_evidence_digest": evidence,
                      "auditor_principal_id": "owner", "outcome": "verified-investigation-complete"}
        outcome = self.vault.put_verified(project_id="P", content=canon(resolution))
        self.assertEqual(self.svc.resolveOversightCase(self.owner, "P", case_id, {"request_id": "o2", "resolution_digest": outcome})["state"], "resolved")
        self.rejected("conflict", lambda: self.svc.resolveOversightCase(self.owner, "P", case_id, {"request_id": "o3", "resolution_digest": outcome}))
        self.assertEqual(len(self.store.events), 2)
        self.assertEqual(self.svc.listOversightCases(self.owner, "P")["cases"][0]["state"], "resolved")

    def test_15_forged_oversight_resolution_rejected(self):
        evidence = self.vault.put_verified(project_id="P", content=b'actual evidence')
        case = self.svc.openOversightCase(self.owner, "P", {"request_id": "o1", "subject_id": "sub", "evidence_digest": evidence})["case_id"]
        for value in ({"project_id": "Q", "case_id": case, "subject_id": "sub", "initial_evidence_digest": evidence, "auditor_principal_id": "owner", "outcome": "pass"},
                      {"project_id": "P", "case_id": case, "subject_id": "sub", "initial_evidence_digest": evidence, "auditor_principal_id": "author", "outcome": "pass"}):
            d = self.vault.put_verified(project_id="P", content=canon(value))
            self.rejected("conflict", lambda: self.svc.resolveOversightCase(self.owner, "P", case, {"request_id": "different-" + d, "resolution_digest": d}))

    def test_16_unavailable_oversight_evidence_rejected(self):
        self.rejected("missing", lambda: self.svc.openOversightCase(self.owner, "P", {"request_id": "o1", "subject_id": "sub", "evidence_digest": "a"*64}))

    def test_17_nonhuman_without_lineage_rejected(self):
        self.directory.selected = ReviewerAssignment("independent", None)
        self.rejected("forbidden", self.request)

    def test_18_wrong_request_shape_rejected(self):
        self.rejected("invalid", lambda: self.svc.requestReview(self.author, "P", {"request_id": "r", "submission_id": "sub", "acceptance_digest": self.acceptance, "reviewer_id": "author"}))

    def test_19_missing_original_artifact_rejected(self):
        self.sql("DELETE FROM submission_artifacts WHERE submission_id='sub'")
        self.rejected("missing", self.request)

    def test_20_worker_untrusted_claim_cannot_override_real_verdict(self):
        rid = self.request()
        self.validators.status = "unavailable"
        self.assertEqual(self.verdict(rid, verdict="pass")["verdict"], "incomplete")
        self.assertEqual(self.validators.calls, 1)


    def test_21_oversight_unknown_subject_rejected(self):
        evidence = self.vault.put_verified(project_id="P", content=b'evidence')
        self.rejected("missing", lambda: self.svc.openOversightCase(self.owner, "P", {"request_id": "new", "subject_id": "does-not-exist", "evidence_digest": evidence}))

    def test_22_investigation_transition_and_resolution(self):
        evidence = self.vault.put_verified(project_id="P", content=b'evidence')
        case = self.svc.openOversightCase(self.owner, "P", {"request_id": "open", "subject_id": "sub", "evidence_digest": evidence})["case_id"]
        result = self.svc.beginInvestigation(self.owner, "P", case, "investigate")
        self.assertEqual(result["state"], "investigating")
        self.assertEqual(self.svc.beginInvestigation(self.owner, "P", case, "investigate"), result)
        self.rejected("conflict", lambda: self.svc.beginInvestigation(self.owner, "P", case, "different"))
        resolution = {"project_id": "P", "case_id": case, "subject_id": "sub", "initial_evidence_digest": evidence,
                      "auditor_principal_id": "owner", "outcome": "investigated"}
        digest_value = self.vault.put_verified(project_id="P", content=canon(resolution))
        self.assertEqual(self.svc.resolveOversightCase(self.owner, "P", case, {"request_id": "resolve", "resolution_digest": digest_value})["state"], "resolved")

    def test_23_escalation_from_incomplete_and_not_from_pass(self):
        rid = self.request()
        self.validators.status = "unavailable"
        self.verdict(rid)
        case = self.svc.escalateReview(self.owner, "P", rid, "escalation")
        self.assertEqual(case["state"], "open")
        # After independent PASS, escalation is not an alternate verdict path.
        self.validators.status = "pass"
        rid2 = self.request("r2")
        self.verdict(rid2, request_id="v2")
        self.rejected("conflict", lambda: self.svc.escalateReview(self.owner, "P", rid2, "esc2"))


    def test_24_reviewer_finding_can_fail_validator_pass(self):
        rid = self.request()
        bound_digest = self.conn.execute("SELECT evidence_digest FROM qa_reviews WHERE review_id=?", (rid,)).fetchone()[0]
        report = {"kind": "review_finding", "project_id": "P", "review_id": rid, "source_revision_id": "rev",
                  "reviewer_principal_id": "reviewer", "binding_digest": bound_digest,
                  "verdict": "fail", "reason": "Reviewer independently reproduced a behavioral failure"}
        finding = self.vault.put_verified(project_id="P", content=canon(report))
        self.assertEqual(self.verdict(rid, verdict="fail", evidence_digests=[finding])["verdict"], "fail")
        self.rejected("qa_failed", lambda: self.svc.requireExactIndependentPass(self.owner, "P", "sub", "rev"))

    def test_25_reviewer_forged_finding_rejected(self):
        rid = self.request()
        bound_digest = self.conn.execute("SELECT evidence_digest FROM qa_reviews WHERE review_id=?", (rid,)).fetchone()[0]
        report = {"kind": "review_finding", "project_id": "P", "review_id": rid, "source_revision_id": "rev",
                  "reviewer_principal_id": "author", "binding_digest": bound_digest,
                  "verdict": "fail", "reason": "forged"}
        finding = self.vault.put_verified(project_id="P", content=canon(report))
        self.rejected("conflict", lambda: self.verdict(rid, verdict="fail", evidence_digests=[finding]))


    def test_26_review_verdict_idempotent_replay(self):
        rid = self.request()
        result = self.verdict(rid)
        self.assertEqual(result, self.verdict(rid))
        self.assertEqual(len(self.store.events), 2)
        self.rejected("conflict", lambda: self.verdict(rid, verdict="fail"))

    def test_27_append_artifact_after_pass_invalidates_exact_gate(self):
        rid = self.request()
        self.verdict(rid)
        self.put_artifact("later", self.source_bytes)
        self.sql("INSERT INTO submission_artifacts(project_id,submission_id,artifact_id) VALUES('P','sub','later')")
        self.rejected("qa_failed", lambda: self.svc.requireExactIndependentPass(self.owner, "P", "sub", "rev"))

    def test_28_missing_original_transcript_invalidates_pass(self):
        rid = self.request()
        self.verdict(rid)
        verdict_evidence = self.conn.execute("SELECT evidence_digest FROM qa_reviews WHERE review_id=?", (rid,)).fetchone()[0]
        transcript = json.loads(self.vault.get(project_id="P", digest=verdict_evidence))["receipts"][0]["transcript_digest"]
        del self.vault.content["P", transcript]
        self.rejected("qa_failed", lambda: self.svc.requireExactIndependentPass(self.owner, "P", "sub", "rev"))

    def test_29_independence_proof_frozen_after_pass(self):
        rid = self.request()
        self.verdict(rid)
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql("UPDATE qa_independence_proofs SET independence_evidence_digest='forged' WHERE review_id=?", (rid,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
