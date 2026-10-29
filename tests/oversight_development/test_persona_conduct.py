import ast
from pathlib import Path
import unittest

from oversight_development.api import build_component
from oversight_development.conduct import (
    Behavior, ConductObservation, assess_conduct,
)
from oversight_development.models import EvidenceRef, Verification
from oversight_development.persona import (
    PersonaSnapshot, canonical_digest, propose_persona_revision,
)
from test_competency import FakeVerifier


class FakeRegistry:
    def __init__(self):
        self.reads = 0
        self.writes = 0
        self.current = PersonaSnapshot("worker-7", 3,
                  canonical_digest(worker_id="worker-7", version=3,
                                   style={"skepticism": 70, "directness": 65}),
                  {"skepticism": 70, "directness": 65})

    def read_current(self, worker_id):
        self.reads += 1
        return self.current

    def write_profile(self, *_args, **_kwargs):
        self.writes += 1
        raise AssertionError("NO PERSONA WRITE IS PERMITTED")


class ExplodingExternalWriter:
    def __getattr__(self, _):
        raise AssertionError("No external provider should be consulted or mutated")


class PersonaConductTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.kwargs = dict(worker_id="worker-7", expected_version=3,
                           expected_digest=self.registry.current.digest,
                           style_delta={"skepticism": 85}, evidence_digest="a" * 64)

    def propose(self, **override):
        return propose_persona_revision(**{**self.kwargs, **override}, registry=self.registry)

    def test_proposes_digest_and_version_without_registry_write(self):
        outcome = self.propose()
        self.assertEqual(outcome.status, "PROPOSED")
        self.assertEqual(outcome.value.current_version, 3)
        self.assertEqual(outcome.value.proposed_version, 4)
        self.assertNotEqual(outcome.value.proposed_digest, outcome.value.current_digest)
        self.assertEqual(outcome.value.status, "OWNER_REVIEW_REQUIRED")
        self.assertEqual(self.registry.writes, 0)

    def test_permission_widening_denied(self):
        for forbidden in ("launch_permissions", "permissions", "role", "skip_tests",
                          "tests", "tool_access", "max_jobs"):
            with self.subTest(forbidden=forbidden):
                self.assertEqual(self.propose(style_delta={forbidden: 1}).status, "DENIED")
        self.assertEqual(self.registry.writes, 0)

    def test_stale_digest_replay_denied(self):
        self.assertEqual(self.propose(expected_digest="b" * 64).status, "DENIED")
        self.assertEqual(self.propose(expected_version=2).status, "DENIED")

    def test_unavailable_port_fails_closed(self):
        self.assertEqual(propose_persona_revision(**self.kwargs, registry=None).status,
                         "UNSUPPORTED")

    def test_style_out_of_range_denied(self):
        self.assertEqual(self.propose(style_delta={"exploration": 101}).status, "DENIED")
        self.assertEqual(self.propose(style_delta={"exploration": True}).status, "DENIED")

    def test_noop_style_patch_denied(self):
        self.assertEqual(self.propose(style_delta={"skepticism": 70}).status, "DENIED")

    def test_profile_snapshot_digest_tampering_denied(self):
        with self.assertRaisesRegex(ValueError, "not consistent"):
            PersonaSnapshot("worker-7", 3, "1" * 64, {"skepticism": 70})

    def test_registry_failure_is_unsupported(self):
        class Faulted:
            def read_current(self, _):
                raise OSError("unavailable")
        self.assertEqual(propose_persona_revision(**self.kwargs,
                         registry=Faulted()).status, "UNSUPPORTED")

    def test_skepticism_routes_technical_not_conduct(self):
        obs = ConductObservation("obs-1", "worker-7", Behavior.EVIDENCED_SKEPTICISM,
                                 EvidenceRef("record-1", "1" * 64), "context-1")
        result = assess_conduct((obs,), verifier=FakeVerifier())
        self.assertEqual(result.route, "TECHNICAL_REVIEW")
        self.assertEqual(result.authorization_effect, "NONE")

    def test_unprofessional_observable_message_route_not_sanction(self):
        obs = ConductObservation("obs-2", "worker-7", Behavior.UNPROFESSIONAL_COMMUNICATION,
                                 EvidenceRef("record-2", "2" * 64), "context-2")
        result = assess_conduct((obs,), verifier=FakeVerifier())
        self.assertEqual(result.route, "CONDUCT_REVIEW_RECOMMENDED")
        self.assertEqual(result.authorization_effect, "NONE")

    def test_forgery_routes_security_review_only(self):
        obs = ConductObservation("obs-3", "worker-7", Behavior.FORGED_AUTHORITY,
                                 EvidenceRef("record-3", "3" * 64), "context-3")
        result = assess_conduct((obs,), verifier=FakeVerifier())
        self.assertEqual(result.route, "SECURITY_REVIEW_RECOMMENDED")
        self.assertEqual(result.authorization_effect, "NONE")

    def test_unverified_allegation_no_conduct_finding(self):
        obs = ConductObservation("obs-4", "worker-7", Behavior.FORGED_AUTHORITY,
                                 EvidenceRef("record-4", "4" * 64), "context-4")
        result = assess_conduct((obs,), verifier=FakeVerifier(Verification.REJECTED))
        self.assertEqual(result.route, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result.unverified_count, 1)

    def test_conduct_replay_denied(self):
        obs = ConductObservation("same", "worker-7", Behavior.TECHNICAL_DISAGREEMENT,
                                 EvidenceRef("record-1", "1" * 64), "context-1")
        with self.assertRaisesRegex(ValueError, "replayed"):
            assess_conduct((obs, obs), verifier=FakeVerifier())

    def test_no_implicit_provider_writes_or_game_specific_imports(self):
        # AST verification of production module imports, not a claim of live host audit.
        source_dir = Path(__file__).resolve().parents[2] / "oversight_development"
        forbidden = ("godot", "fmod", "terrain3d", "gameproduction", "github", "requests",
                     "subprocess", "socket", "googleapiclient")
        for file in source_dir.glob("*.py"):
            with self.subTest(file=file.name):
                tree = ast.parse(file.read_text())
                imports = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.extend(alias.name.lower() for alias in node.names)
                    if isinstance(node, ast.ImportFrom) and node.module:
                        imports.append(node.module.lower())
                self.assertFalse(any(any(item.startswith(prefix) for prefix in forbidden)
                                     for item in imports))
        # No production port has a write method; unrecognized injected dependencies ignored.
        component = build_component({"writer": ExplodingExternalWriter()})
        self.assertEqual(component.execute("hr.launch_worker", {}).status, "UNSUPPORTED")
        self.assertEqual(component.execute("hr.coaching_plan", {}).status, "DENIED")

    def test_missing_reader_explicit_unsupported(self):
        component = build_component({})
        self.assertEqual(component.propose_persona(**self.kwargs).status, "UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
