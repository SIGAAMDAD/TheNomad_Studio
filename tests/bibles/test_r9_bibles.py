"""Actual frozen-DDL R9 CAB-D tests, including negative owner/epoch/scope cases."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import unittest

from nomad_studio.bibles import BibleRegistry, Decision
from nomad_studio.bibles.model import BibleError, canonical, digest
from nomad_studio.governance import AuthorityService
from nomad_studio.single_operator.ownercontrol import OwnerProofGateway

# Contracts are shipped in the worker kit, not installed as runtime schema data.
SCHEMA = Path(__file__).resolve().parent / "fixtures" / "initial_schema_v1.sql"


class DB:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        assert hashlib.sha256(SCHEMA.read_bytes()).hexdigest() == "f2a76f51fef851fe972500c906f31cde84bf63e446a4a149ab6ac08c9fe410f5"
        self.connection.executescript(SCHEMA.read_text())

    def execute(self, sql, params=()):
        self.connection.execute(sql, params)

    def fetch_one(self, sql, params=()):
        return self.connection.execute(sql, params).fetchone()

    def fetch_all(self, sql, params=()):
        return self.connection.execute(sql, params).fetchall()

    def transact(self, callback):
        self.connection.execute("SAVEPOINT cabd")
        try:
            result = callback(self)
            self.connection.execute("RELEASE cabd")
            return result
        except BaseException:
            self.connection.execute("ROLLBACK TO cabd")
            self.connection.execute("RELEASE cabd")
            raise

    def read(self, callback):
        return callback(self)


class Source:
    def __init__(self):
        self.items = {}

    def add(self, kind, epoch=1, project_id="proj", prefix=""):
        eid = prefix + kind.lower() + "-" + str(epoch)
        base = kind.lower() + "/" + str(epoch)
        main, rule, annex = base + "/main.md", base + "/rule.md", base + "/annex.md"
        body = b"# full bible\n"
        text = (kind + " required byte check").encode()
        annex_data = b"# annex\n"
        files = {main: body, rule: text, annex: annex_data}
        manifest = {"version": 1, "repo": "repo", "source_commit": "a" * 40,
                    "files": [{"path": n, "sha256": hashlib.sha256(b).hexdigest(),
                               "role": "ROOT" if n == main else "CLAUSE" if n == rule else "ANNEX"}
                              for n, b in sorted(files.items())]}
        raw = canonical(manifest)
        clause = {"clause_id": "REQUIRED", "kind": "REQUIREMENT", "scope": "src/**",
                  "text_digest": hashlib.sha256(text).hexdigest(), "precedence": 50,
                  "source_ref": rule}
        edition = {"project_id": project_id, "kind": kind, "edition_id": eid, "epoch": epoch,
                   "manifest_digest": hashlib.sha256(raw).hexdigest(), "repo": "repo",
                   "source_commit": "a" * 40, "annex_digests": [hashlib.sha256(annex_data).hexdigest()],
                   "clauses": [clause]}
        self.items[eid] = {"manifest_bytes": raw, "files": files,
                           "source_commit": "a" * 40, "repo": "repo", "readback_ref": eid + ":source"}
        return edition

    def __call__(self, repo, commit, request):
        return self.items[request["edition_id"]]


class Identity:
    def __init__(self):
        self.records = {}

    def issue(self, action, subject, project, affected, *, expired=False):
        ref = "proof-" + str(len(self.records) + 1)
        record = {"proof_ref": ref, "decision_id": "decision-" + str(len(self.records) + 1),
                  "owner_id": "owner", "project_id": project, "action": action,
                  "subject_digest": subject, "authority_epoch": 1,
                  "expires_at_us": 1 if expired else 1000000}
        self.records[ref] = record
        return Decision(True, "ALLOW", "OK", affected, (ref,))

    def verify_owner_decision(self, ref, action, subject, project):
        a = self.records.get(ref)
        if a and (a["action"], a["subject_digest"], a["project_id"]) == (action, subject, project):
            return Decision(True, "ALLOW", "OK", self.effects[ref], (ref,))
        return Decision(False, "DENY", "EVIDENCE_INVALID", "", ())

    effects = {}

    def register(self, action, subject, project, affected, **kw):
        proof = self.issue(action, subject, project, affected, **kw)
        self.effects[proof.evidence_refs[0]] = affected
        return proof


class Compliance:
    def __init__(self):
        self.result = "PASS"
        self.omit_requirement = False
        self.calls = 0

    def __call__(self, intent, checked, clauses):
        self.calls += 1
        refs = [c["ref"] for c in clauses]
        return {"project_id": intent["project_id"], "feature_id": intent["feature_id"],
                "bible_pair_digest": intent["bible_pair_digest"],
                "subject_digest": intent["payload_digest"],
                "clause_refs_checked": (refs[:-1] if self.omit_requirement else refs),
                "violations": refs[:1] if self.result == "FAIL" else [],
                "semantic_review_ref": "independent-semantic-proof", "mechanical_check_refs": ["check-001"],
                "result": self.result}


class BibleAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.db = DB()
        self.db.execute("INSERT INTO v1_projects VALUES (?,?,?,?,?)", ("proj", "owner", "c" * 64, "ACTIVE", 1))
        self.db.execute("INSERT INTO v1_projects VALUES (?,?,?,?,?)", ("other", "owner", "c" * 64, "ACTIVE", 1))
        self.source = Source()
        self.identity = Identity()
        self.gateway = OwnerProofGateway(self.identity, self.identity.records.get, lambda: 100)
        self.bibles = BibleRegistry(self.db, self.source, self.gateway)
        self.compliance = Compliance()
        self.auth = AuthorityService(self.db, self.compliance, self.gateway, lambda: 100)
        self.design = self.source.add("DESIGN")
        self.technical = self.source.add("TECHNICAL")

    def tearDown(self):
        self.db.connection.close()

    def propose(self, edition):
        return self.bibles.propose_edition(edition, {"edition_id": edition["edition_id"], "verified": True})

    def activate(self, edition, *, expected=0, forged=False):
        subject = digest({"action": "BIBLE_ACTIVATE", "project_id": edition["project_id"],
                          "edition_id": edition["edition_id"], "kind": edition["kind"],
                          "epoch": edition["epoch"], "manifest_digest": edition["manifest_digest"]})
        proof = self.identity.register("BIBLE_ACTIVATE", subject, edition["project_id"], edition["edition_id"])
        if forged:
            proof = Decision(True, "ALLOW", "OK", edition["edition_id"], ("fake",))
        return self.bibles.activate_edition(edition["edition_id"], proof, expected)

    def active(self):
        for edition in (self.design, self.technical):
            self.assertEqual(self.propose(edition).code, "OK")
            self.assertEqual(self.activate(edition).code, "OK")
        pair = self.bibles.snapshot_active_pair("proj", {"project_id": "proj"})
        self.assertEqual(pair.code, "OK")
        return pair.record["pair_digest"]

    def feature(self, pair):
        db = self.db
        db.execute("INSERT INTO v1_briefs (project_id,brief_id,feature_id,owner_principal_id,original_text,original_digest,owner_command_ref,submitted_at_us) VALUES (?,?,?,?,?,?,?,?)",
                   ("proj", "brief", "feature", "owner", "short brief", "b" * 64, "cmd", 1))
        db.execute("INSERT INTO v1_feature_contracts (project_id,feature_id,revision,brief_id,original_brief_digest,bible_pair_digest,contract_digest,scope_digest,deliverables_digest,acceptance_digest,budget_limit_units,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("proj", "feature", 1, "brief", "b" * 64, pair, "c" * 64, "d" * 64,
                    "e" * 64, "f" * 64, 1000, json.dumps({"repository_ref": "repo", "feature_branch": "features/task"})))
        db.execute("INSERT INTO v1_feature_runs (project_id,feature_id,brief_id,active_contract_digest,bible_pair_digest,phase,revision,graph_revision,updated_at_us) VALUES (?,?,?,?,?,?,?,?,?)",
                   ("proj", "feature", "brief", "c" * 64, pair, "IN_PRODUCTION", 1, 1, 100))
        db.execute("INSERT INTO v1_source_leases (feature_id,lease_id,repo,branch,resource_path,resource_path_key,expected_parent_commit,active,expires_at_us) VALUES (?,?,?,?,?,?,?,?,?)",
                   ("feature", "lease", "repo", "features/task", "src", "src", "a" * 40, 1, 1000000))
        intent = {"effect_id": "effect", "project_id": "proj", "feature_id": "feature",
                  "action": "FEATURE_BRANCH_WRITE", "target_ref": "src/scene.gd",
                  "bible_pair_digest": pair, "feature_contract_digest": "c" * 64,
                  "scope_digest": "d" * 64, "payload_digest": "e" * 64,
                  "idempotency_key": "key", "expected_source_commit": "a" * 40}
        return intent

    def checked(self):
        return {"scope": "src/scene.gd", "subject_digest": "e" * 64,
                "review_input_ref": "observed-source-readback"}

    def authorize(self, intent):
        return self.auth.authorize_effect(intent, bible_pair_digest=intent["bible_pair_digest"],
                                          contract_digest=intent["feature_contract_digest"],
                                          checked_content=self.checked())

    def test_propose_requires_real_full_byte_manifest(self):
        self.assertEqual(self.propose(self.design).code, "OK")
        forged = self.source.add("TECHNICAL")
        self.source.items[forged["edition_id"]]["files"].pop("technical/1/annex.md")
        self.assertEqual(self.propose(forged).code, "EVIDENCE_INVALID")
        self.assertIsNone(self.db.fetch_one("SELECT * FROM v1_bible_editions WHERE edition_id=?", (forged["edition_id"],)))

    def test_byte_tamper_detected(self):
        self.source.items[self.design["edition_id"]]["files"]["design/1/rule.md"] = b"forged"
        self.assertEqual(self.propose(self.design).code, "SOURCE_CONFLICT")

    def test_owner_authentication_and_epoch_compare_and_set(self):
        self.assertEqual(self.propose(self.design).code, "OK")
        self.assertEqual(self.activate(self.design, forged=True).code, "EVIDENCE_INVALID")
        self.assertEqual(self.activate(self.design, expected=1).code, "STALE_BIBLE")
        self.assertEqual(self.activate(self.design).code, "OK")
        self.assertEqual(self.activate(self.design).code, "REVISION_CONFLICT")

    def test_missing_pair_and_cross_project(self):
        self.assertEqual(self.propose(self.design).code, "OK")
        self.assertEqual(self.activate(self.design).code, "OK")
        self.assertEqual(self.bibles.snapshot_active_pair("proj", {"project_id": "proj"}).code, "STALE_BIBLE")
        self.assertEqual(self.bibles.snapshot_active_pair("proj", {"project_id": "other"}).code, "WRONG_PROJECT")

    def test_in_bible_major_decision_no_owner_approval(self):
        pair = self.active()
        intent = self.feature(pair)
        self.assertEqual(self.authorize(intent).status, "ALLOW")
        assessed = self.auth.assess_bible_conflict("feature", {"project_id": "proj", **self.checked()}, pair)
        self.assertEqual(assessed.code, "OK")
        self.assertEqual(assessed.record["result"], "PASS")

    def test_requirement_conflict_explicit_clause_refs(self):
        pair = self.active()
        intent = self.feature(pair)
        self.compliance.result = "FAIL"
        decided = self.authorize(intent)
        self.assertEqual(decided.code, "BIBLE_CONFLICT")
        self.assertEqual(decided.evidence_refs, ("design-1:REQUIRED",))
        self.assertEqual(self.auth.assess_bible_conflict("feature", {"project_id": "proj", **self.checked()}, pair).code,
                         "BIBLE_CONFLICT")

    def test_unchecked_required_clause_does_not_pass(self):
        pair = self.active()
        intent = self.feature(pair)
        self.compliance.omit_requirement = True
        # Two requirements (Design/Technical), dropping one rejects a fake PASS.
        self.assertEqual(self.authorize(intent).code, "EVIDENCE_INVALID")

    def test_stale_edition_fenced_at_effect_time(self):
        pair = self.active()
        intent = self.feature(pair)
        next_design = self.source.add("DESIGN", 2)
        self.assertEqual(self.propose(next_design).code, "OK")
        self.assertEqual(self.activate(next_design, expected=1).code, "OK")
        self.assertEqual(self.authorize(intent).code, "STALE_BIBLE")
        self.assertEqual(self.bibles.get_clause_bundle(pair, {"project_id": "proj", "scope": "src/scene.gd"}).code,
                         "STALE_BIBLE")

    def test_lease_and_scope_are_hard_effect_fences(self):
        pair = self.active()
        intent = self.feature(pair)
        self.db.execute("UPDATE v1_source_leases SET active=0 WHERE lease_id='lease'")
        self.assertEqual(self.authorize(intent).code, "LEASE_FENCED")
        self.db.execute("UPDATE v1_source_leases SET active=1 WHERE lease_id='lease'")
        intent["scope_digest"] = "f" * 64
        self.assertEqual(self.authorize(intent).code, "SCOPE_EXCEEDED")
        intent["scope_digest"] = "d" * 64
        intent["target_ref"] = "../escape"
        self.assertEqual(self.authorize(intent).code, "INVALID_INPUT")

    def test_protected_effect_owner_exact_subject_and_replay_fence(self):
        pair = self.active()
        intent = self.feature(pair)
        intent["action"] = "RELEASE_PUBLISH"
        self.assertEqual(self.authorize(intent).code, "OWNER_ACTION_REQUIRED")
        intent["owner_proof_ref"] = "proof-protected"
        fake = Decision(True, "ALLOW", "OK", "effect", ("proof-protected",))
        self.assertEqual(self.auth.authorize_protected_effect(intent, fake).code, "EVIDENCE_INVALID")
        subject = digest(intent)
        proof = self.identity.register("RELEASE_PUBLISH", subject, "proj", "effect")
        intent["owner_proof_ref"] = proof.evidence_refs[0]
        # Proof digest must bind the final (incl proof ref) intent bytes.
        self.identity.records[proof.evidence_refs[0]]["subject_digest"] = digest(intent)
        self.assertEqual(self.auth.authorize_protected_effect(intent, proof).status, "ALLOW")
        self.assertEqual(self.auth.authorize_protected_effect(intent, proof).code, "EVIDENCE_INVALID")

    def test_scope_from_claim_cannot_narrow_applicable_rules(self):
        pair = self.active()
        intent = self.feature(pair)
        supplied = {**self.checked(), "scope": "docs/readme.md"}
        decision = self.auth.authorize_effect(intent, bible_pair_digest=pair,
                         contract_digest=intent["feature_contract_digest"], checked_content=supplied)
        self.assertEqual(decision.code, "SCOPE_EXCEEDED")
        self.assertEqual(self.compliance.calls, 0)

    def test_expired_owner_evidence_and_wrong_owner_id(self):
        self.assertEqual(self.propose(self.design).code, "OK")
        subject = digest({"action": "BIBLE_ACTIVATE", "project_id": "proj",
                          "edition_id": "design-1", "kind": "DESIGN", "epoch": 1,
                          "manifest_digest": self.design["manifest_digest"]})
        proof = self.identity.register("BIBLE_ACTIVATE", subject, "proj", "design-1", expired=True)
        self.assertEqual(self.bibles.activate_edition("design-1", proof, 0).code, "EVIDENCE_INVALID")
        valid = self.identity.register("BIBLE_ACTIVATE", subject, "proj", "design-1")
        self.identity.records[valid.evidence_refs[0]]["owner_id"] = "impostor"
        self.assertEqual(self.bibles.activate_edition("design-1", valid, 0).code, "EVIDENCE_INVALID")
        self.assertEqual(self.db.fetch_one("SELECT COUNT(*) AS n FROM v1_owner_decisions")["n"], 0)

    def test_lease_revoked_while_independent_verifier_runs(self):
        pair = self.active()
        intent = self.feature(pair)
        existing = self.compliance
        def revoker(*args):
            result = existing(*args)
            self.db.execute("UPDATE v1_source_leases SET active=0 WHERE lease_id='lease'")
            return result
        self.auth.compliance_verifier = revoker
        self.assertEqual(self.authorize(intent).code, "LEASE_FENCED")

    def test_wrong_feature_and_project_scope_rejected(self):
        pair = self.active()
        intent = self.feature(pair)
        intent["feature_id"] = "foreign"
        self.assertEqual(self.authorize(intent).code, "WRONG_FEATURE")
        intent["feature_id"] = "feature"
        intent["project_id"] = "other"
        self.assertIn(self.authorize(intent).code, ("STALE_BIBLE", "WRONG_FEATURE"))

    def test_non_string_action_and_malformed_receipt_are_rejected(self):
        pair = self.active()
        intent = self.feature(pair)
        intent["action"] = {"forged": "CREATIVE_MAJOR"}
        self.assertEqual(self.authorize(intent).code, "INVALID_INPUT")
        intent["action"] = "FEATURE_BRANCH_WRITE"
        def broken(*args):
            record = self.compliance(*args)
            record["violations"] = [["bad-nested-list"]]
            return record
        self.auth.compliance_verifier = broken
        self.assertEqual(self.authorize(intent).code, "EVIDENCE_INVALID")

    def test_get_clause_bundle_and_provenance(self):
        pair = self.active()
        bundle = self.bibles.get_clause_bundle(pair, {"project_id": "proj", "scope": "src/scene.gd"})
        self.assertEqual({c["ref"] for c in bundle.record["clauses"]},
                         {"design-1:REQUIRED", "technical-1:REQUIRED"})
        self.assertTrue(self.bibles.verify_clause_provenance(["technical-1:REQUIRED"], pair).allowed)
        denied = self.bibles.verify_clause_provenance(["other-1:REQUIRED"], pair)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.code, "BIBLE_CONFLICT")
        self.assertEqual(self.db.connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
