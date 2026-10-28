"""WG-A offline/schema/negative conformance tests; deliberately NOT a gateway.

Run: python -m unittest discover -s tests/v16/contracts -p 'test_*.py' -v
External dependency: jsonschema>=4.18 with referencing (draft 2020-12).
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

try:
    from .reference_policy import evaluate, verify_publication
except ImportError:  # standalone -s tests/v16/contracts discovery
    from reference_policy import evaluate, verify_publication

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "studio/v16/schemas"
CONTRACTS = ROOT / "studio/v16/contracts"
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def registry_and_schemas():
    docs = {}
    resources = []
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        raw = json.loads(path.read_text())
        Draft202012Validator.check_schema(raw)
        key = path.name.removesuffix(".v1.schema.json")
        docs[key] = raw
        resources.append((raw["$id"], Resource.from_contents(raw)))
    return Registry().with_resources(resources), docs


class WGAFrozenContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry, cls.schemas = registry_and_schemas()
        cls.validators = {k: Draft202012Validator(doc, registry=cls.registry,
                                                   format_checker=FormatChecker())
                          for k, doc in cls.schemas.items()}

    def test_contracts_are_self_describing_and_versioned(self):
        self.assertEqual(len(self.validators), 12)
        for key, doc in self.schemas.items():
            self.assertEqual(doc["$id"], f"urn:nomad:studio:v16:{key}:1")
            self.assertEqual(doc["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertFalse(doc["additionalProperties"])
            if key != "common":
                self.assertIn("schema_id", doc["required"])
                self.assertEqual(len(doc["properties"]["schema_id"]["const"].split("/")), 3)

    def test_freeze_manifest_matches_every_contract_file(self):
        manifest = json.loads((CONTRACTS / "FREEZE_MANIFEST.v1.json").read_text())
        self.assertEqual(manifest["status"], "CANDIDATE_PENDING_OWNER_ARCHITECT_QA")
        members = manifest["files"]
        self.assertEqual(len(members), 19)  # 12 schemas + six docs + retirement
        self.assertEqual([e["path"] for e in members], sorted(e["path"] for e in members))
        for item in members:
            with self.subTest(file=item["path"]):
                self.assertEqual(hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest(), item["sha256"])
        canonical = "".join(e["path"] + "\0" + e["sha256"] + "\n" for e in members).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), manifest["bundle_sha256"])

    def test_examples_parse_accurately(self):
        examples = load("positive.json")
        self.assertGreaterEqual(len(examples), 20)
        for case, value in examples.items():
            key = next(k for k in self.validators if case == k or case.startswith(k + "-"))
            with self.subTest(case=case, schema=key):
                self.validators[key].validate(value)

    def test_invalid_vectors_are_structurally_rejected(self):
        vectors = load("schema_invalid.json")
        self.assertGreaterEqual(len(vectors), 25)
        for row in vectors:
            with self.subTest(case=row["case"]):
                self.assertIn(row["schema"], self.validators)
                errors = list(self.validators[row["schema"]].iter_errors(row["value"]))
                self.assertTrue(errors, f"INVALID vector parsed: {row['case']}")

    def test_authorization_deny_matrix(self):
        v = load("authorization_vectors.json")
        req = v["untrusted_request"]
        ctx = v["synthetic_trusted_gateway_state"]
        self.validators["auth-evaluate"].validate(req)
        self.assertEqual(len(v["cases"]), len({c["case"] for c in v["cases"]}))
        self.assertNotIn("ALLOW", {c["expected"] for c in v["cases"]})
        for row in v["cases"]:
            candidate = dict(req, **row["request_overrides"])
            trusted = dict(ctx, **row["trusted_context_overrides"])
            with self.subTest(case=row["case"]):
                self.assertEqual(evaluate(candidate, trusted), row["expected"])

    def test_schema_valid_cannot_mean_authorized(self):
        req = copy.deepcopy(load("positive.json")["auth-evaluate"])
        self.validators["auth-evaluate"].validate(req)
        ctx = dict(load("authorization_vectors.json")["synthetic_trusted_gateway_state"])
        ctx["credential_verified"] = False
        self.assertEqual(evaluate(req, ctx), "IDENTITY_UNVERIFIED")
        ctx["credential_verified"] = True
        ctx["source_writes_enabled"] = False
        self.assertEqual(evaluate(req, ctx), "CUTOVER_NOT_APPROVED")
        # The fixture with synthetic verified credentials is a non-deployed model.

    def test_publication_negative_vectors(self):
        v = load("publication_vectors.json")
        self.assertNotIn("ALLOW", {c["expected"] for c in v["cases"] + v["full_cases"]})
        for row in v["cases"]:
            intent = copy.deepcopy(v["intent"])
            receipt = copy.deepcopy(v["receipt"])
            trusted = dict(v["synthetic_trusted_gateway_state"])
            intent.update(row["intent_overrides"])
            receipt.update(row["receipt_overrides"])
            trusted.update(row["trusted_context_overrides"])
            with self.subTest(case=row["case"]):
                self.assertEqual(verify_publication(intent, receipt, trusted), row["expected"])
        for row in v["full_cases"]:
            with self.subTest(case=row["case"]):
                self.validators["publish-intent"].validate(row["intent"])
                self.validators["publish-readback"].validate(row["receipt"])
                self.assertEqual(verify_publication(row["intent"], row["receipt"], row["trusted_context"]), row["expected"])

    def test_legacy_retirement_is_nonexecutable_and_complete(self):
        routes = json.loads((CONTRACTS / "legacy-retirement.v1.json").read_text())
        self.assertFalse(routes["active_ai_chat_dispatch"])
        self.assertEqual(routes["dispatch_capability"], "NONE")
        self.assertEqual(routes["allowed_launch_method"], "USER_OPENED_MANUAL_CHAT")
        self.assertEqual([r["legacy_profile"] for r in routes["historical_routes"]],
                         [f"W{i:02}" for i in range(1, 6)])
        self.assertEqual(len({r["historical_label"] for r in routes["historical_routes"]}), 5)
        self.assertEqual(routes["historical_delivery_authority"], "ARCHIVE_ONLY")

    def test_positive_run_and_event_cannot_autolaunch(self):
        positive = load("positive.json")
        for name, event in positive.items():
            if name.startswith("run-startup"):
                self.assertEqual(event["launch_mode"], "USER_OPENED_MANUAL_CHAT")
                self.assertFalse(event["external_connector_called"])
            if name.startswith("db-event"):
                self.assertEqual(event["delivery_authority"], "ARCHIVE_ONLY")
            if name.startswith("db-command"):
                self.assertNotIn("LAUNCH_CHAT", event["command_kind"])
        self.assertEqual(positive["run-startup"]["access_preflight"], [])

    def test_db_effect_keys_fence_duplicates_and_epochs(self):
        """Synthetic key properties, not an execution of PostgreSQL/DB implementation."""
        seen_commands = {}
        seen_effects = set()
        def reserve(command_id, digest):
            existing = seen_commands.get(command_id)
            if existing is not None:
                return "DUPLICATE" if existing == digest else "IDEMPOTENCY_CONFLICT"
            seen_commands[command_id] = digest
            return "COMMITTED"
        def ack(event_id, recipient, epoch, effect, live_epoch):
            if epoch != live_epoch: return "STALE_ASSIGNMENT"
            key = (event_id, recipient, epoch, effect)
            if key in seen_effects: return "DUPLICATE"
            seen_effects.add(key)
            return "COMMITTED"
        self.assertEqual(reserve("cmd-A", "a" * 64), "COMMITTED")
        self.assertEqual(reserve("cmd-A", "a" * 64), "DUPLICATE")
        self.assertEqual(reserve("cmd-A", "b" * 64), "IDEMPOTENCY_CONFLICT")
        self.assertEqual(ack("event-A", "worker-A", 4, "inbox", 4), "COMMITTED")
        self.assertEqual(ack("event-A", "worker-A", 4, "inbox", 4), "DUPLICATE")
        self.assertEqual(ack("event-A", "worker-A", 4, "inbox", 5), "STALE_ASSIGNMENT")

    def test_publish_readback_must_be_bound_to_intent(self):
        """Provider matching is semantic, beyond the published JSON Schema."""
        ex = load("positive.json")
        intent = ex["publish-intent"]
        receipt = ex["publish-readback"]
        self.validators["publish-intent"].validate(intent)
        self.validators["publish-readback"].validate(receipt)
        self.assertEqual(intent["publication_id"], receipt["publication_id"])
        self.assertEqual(intent["idempotency_key"], receipt["idempotency_key"])
        self.assertEqual(intent["artifact_sha256"], receipt["observed"]["artifact_sha256"])
        self.assertEqual(intent["target"]["proposed_tree_sha256"], receipt["observed"]["tree_sha256"])
        self.assertNotEqual(intent["target"]["source_base_sha"], receipt["observed"]["commit_sha"])
        tampered = dict(receipt, observed=dict(receipt["observed"], artifact_sha256="0"*64))
        self.validators["publish-readback"].validate(tampered)
        self.assertNotEqual(intent["artifact_sha256"], tampered["observed"]["artifact_sha256"])
        # WG-G must reject this exact semantic mismatch when it implements readback.


if __name__ == "__main__":
    unittest.main()
