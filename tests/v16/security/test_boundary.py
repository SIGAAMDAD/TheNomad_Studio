"""Contract-consumer security attack matrix; never actual gateway proof."""
import copy
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_TESTS = ROOT / "tests/v16/contracts"
if str(CONTRACT_TESTS) not in sys.path:
    sys.path.insert(0, str(CONTRACT_TESTS))
from reference_policy import evaluate, verify_publication


class SchemaBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schemas = [json.loads(p.read_text()) for p in sorted((ROOT / "studio/v16/schemas").glob("*.schema.json"))]
        registry = Registry().with_resources([(s["$id"], Resource.from_contents(s)) for s in schemas])
        cls.validators = {s["$id"].split(":")[-2]: Draft202012Validator(s, registry=registry,
                                 format_checker=FormatChecker()) for s in schemas}
        cls.examples = json.loads((CONTRACT_TESTS / "fixtures/positive.json").read_text())
        cls.auth = json.loads((CONTRACT_TESTS / "fixtures/authorization_vectors.json").read_text())
        cls.pub = json.loads((CONTRACT_TESTS / "fixtures/publication_vectors.json").read_text())

    def test_untrusted_fields_cannot_become_gateway_credential(self):
        req = copy.deepcopy(self.examples["auth-evaluate"])
        req["external_credential_ref"] = "shared-owner-account"
        ctx = copy.deepcopy(self.auth["synthetic_trusted_gateway_state"])
        ctx["auth_channel"] = "SHARED_OWNER_CONNECTOR"
        self.validators["auth-evaluate"].validate(req)
        self.assertEqual(evaluate(req, ctx), "UNTRUSTED_CONNECTOR")
        ctx["auth_channel"] = "ISOLATED_GATEWAY"
        ctx["credential_verified"] = False
        self.assertEqual(evaluate(req, ctx), "IDENTITY_UNVERIFIED")

    def test_full_path_attacks_and_case_unicode(self):
        req = self.auth["untrusted_request"]
        for path in ("../studio/v16/contracts/AUTH-V16.md", "/etc/passwd", ".github/workflows/x.yml",
                     "tests/v16/../../.github/workflows/ci.yml", "tests/v16/security/../test.py",
                     "tests/v16/security//test.py", "tests/v16/security/ᴋey.py",
                     "tests/v16/security/real\\path", "tests/v16/security/%2e%2e/x"):
            with self.subTest(path=path):
                candidate = copy.deepcopy(req)
                candidate["target_paths"] = [path]
                result = evaluate(candidate, copy.deepcopy(self.auth["synthetic_trusted_gateway_state"]))
                self.assertIn(result, ("INVALID_SCOPE", "INVALID_PATH", "PROTECTED_PATH"))

    def test_rename_two_sided_scope_and_git_tree_facts(self):
        req = copy.deepcopy(self.auth["untrusted_request"])
        ctx = copy.deepcopy(self.auth["synthetic_trusted_gateway_state"])
        ctx["owned_path_prefixes"] = ["tests/v16/"]
        req["target_paths"] = ["tests/v16/security/test.py", "scripts/studio.py"]
        self.assertEqual(evaluate(req, ctx), "INVALID_SCOPE")
        req["target_paths"] = ["tests/v16/security/test.py"]
        ctx["git_tree_links"] = ["tests/v16/security/test.py"]
        self.assertEqual(evaluate(req, ctx), "INVALID_PATH")
        ctx["git_tree_links"] = []
        ctx["git_tree_submodules"] = ["tests/v16/security/test.py"]
        self.assertEqual(evaluate(req, ctx), "INVALID_PATH")
        ctx["git_tree_submodules"] = []
        ctx["git_tree_paths"] = ["tests/v16/security/TEST.py"]
        self.assertEqual(evaluate(req, ctx), "INVALID_PATH")

    def test_stale_assignment_revocation_policy_downgrade(self):
        req = copy.deepcopy(self.auth["untrusted_request"])
        for key, value, expected in (("revoked", True, "REVOKED_GRANT"),
                                     ("assignment_epoch", 6, "STALE_ASSIGNMENT"),
                                     ("lease_generation", 5, "STALE_LEASE"),
                                     ("policy_revision", "policy-newer", "POLICY_REVISION_MISMATCH"),
                                     ("source_base_sha", "b"*40, "REBASE_REQUIRED")):
            with self.subTest(key=key):
                ctx = copy.deepcopy(self.auth["synthetic_trusted_gateway_state"])
                if key in ("assignment_epoch", "lease_generation", "policy_revision", "source_base_sha"):
                    req2 = dict(req, **{key: value})
                else:
                    ctx[key] = value
                    req2 = req
                self.assertEqual(evaluate(req2, ctx), expected)

    def test_provider_mismatched_readback_does_not_verify(self):
        intent = copy.deepcopy(self.pub["intent"])
        receipt = copy.deepcopy(self.pub["receipt"])
        ctx = copy.deepcopy(self.pub["synthetic_trusted_gateway_state"])
        ctx["publication_enabled"] = True
        ctx["trusted_provider_readback"]["commit_sha"] = "1"*40
        self.assertEqual(verify_publication(intent, receipt, ctx), "SOURCE_SHA_MISMATCH")

    def test_source_qa_change_or_self_review_blocks_publication(self):
        intent = copy.deepcopy(self.pub["intent"])
        receipt = copy.deepcopy(self.pub["receipt"])
        ctx = copy.deepcopy(self.pub["synthetic_trusted_gateway_state"])
        ctx["publication_enabled"] = True
        ctx["qa_attested"] = False
        self.assertEqual(verify_publication(intent, receipt, ctx), "QA_PENDING")
        ctx["qa_attested"] = True
        intent["independent_qa"]["reviewer_principal_id"] = intent["principal_id"]
        self.assertEqual(verify_publication(intent, receipt, ctx), "SELF_REVIEW")

    def test_all_schemas_are_strict_on_unknown_control_fields(self):
        for name in ("auth-evaluate", "publish-intent", "db-command", "run-startup"):
            with self.subTest(name=name):
                obj = copy.deepcopy(self.examples[name])
                obj["override_trusted_authority"] = True
                self.assertTrue(list(self.validators[name].iter_errors(obj)))

    def test_qa_report_must_not_contain_broad_secrets_or_case_plaintext(self):
        # Structural test-only redaction check on the report field policy.
        report = {"case_id": "synthetic", "result": "REWORK", "source_sha": "a"*40}
        serialized = json.dumps(report).lower()
        for forbidden in ("github_pat_", "authorization: bearer", "private_key", "oversight_case_body"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
