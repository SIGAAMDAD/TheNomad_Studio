"""Synthetic contracts only. No private repo, keys, case data or external effects."""
import hashlib
import unittest

from oversight_operations.api import build_component
from oversight_operations.models import (Investigator, P0Proof, RestoreReceipt,
                                         RetentionRule, ResultCode, SealedIntake)

CIPHERTEXT = b"test-fixture-ciphertext-format-UNTRUSTED-not-actual-encrypted-data"
CIPHER_SHA = hashlib.sha256(CIPHERTEXT).hexdigest()


def payload(n=1, severity=2, trigger="", received_at=10, retention="STANDARD"):
    return SealedIntake("c_" + f"{n:032x}", "ev_" + f"{n:032x}",
                        f"{n + 400:064x}", CIPHER_SHA, retention,
                        severity, received_at, "sub_" + f"{n:032x}", trigger)


def ports(**changes):
    defaults = dict(
        authorize_case=lambda who, case_id, act, now: who in {"custodian", "investigator"},
        verify_sealed_receipt=lambda sealed_ref, cipher: True,
        private_key_available=lambda: True,
        verify_retention_policy=lambda rule: rule.approved and rule.policy_revision == "p1",
        verify_ciphertext_encryption=lambda case_id, h: h == CIPHER_SHA,
        verify_archive_signature=lambda manifest: manifest.signature_ref == "synthetic_sig",
        verify_restore_witness=lambda receipt: receipt.witness_ref == "machine_witness",
        verify_p0_policy_proof=lambda proof: proof.policy_revision == "P0-test",
        verify_gateway_preapproval=lambda proof: proof.owner_approved_action_ref.startswith("synthetic_"),
    )
    defaults.update(changes)
    return defaults


def retention():
    return RetentionRule("STANDARD", 86400, "p1", "a" * 64, approved=True)


class OperationsTests(unittest.TestCase):
    def test_initial_no_authority_fails_closed_and_keeps_sealed_metadata(self):
        s = build_component()
        result = s.intake(payload(), 10)
        self.assertEqual(result.code, ResultCode.PENDING)
        self.assertFalse(s.cases[payload().case_id].custody_verified)
        self.assertEqual(s.route("custodian", payload().case_id, (), 11).code,
                         ResultCode.BLOCKED_PRIVATE_ACCESS)
        self.assertEqual(s.deliver_alerts(12).code, ResultCode.UNSUPPORTED)
        self.assertTrue(any(a.kind == "NO_PRIVATE_KEY" for a in s.alerts.values()))

    def test_sealed_metadata_not_private_narrative(self):
        s = build_component(ports())
        s.intake(payload(), 10)
        report = s.audit(20)
        self.assertEqual(report.total_cases, 1)
        self.assertFalse(hasattr(report, "case_id"))
        self.assertFalse(hasattr(s.cases[payload().case_id].sealed, "narrative"))
        self.assertNotIn(payload().case_id, str(report))

    def test_bad_payload_denied_not_recorded(self):
        s = build_component(ports())
        invalid = SealedIntake("case-person-name", "ev_" + "0" * 32,
                               "a" * 64, "b" * 64, "STANDARD", 2, 10)
        self.assertEqual(s.intake(invalid, 11).code, ResultCode.DENIED)
        self.assertFalse(s.cases)

    def test_replay_idempotent_conflicting_digest_denied(self):
        s = build_component(ports())
        good = payload()
        self.assertEqual(s.intake(good, 10).code, ResultCode.PENDING)
        self.assertEqual(s.intake(good, 11).reason, "IDEMPOTENT_RECEIPT")
        changed = payload(severity=3)
        self.assertEqual(s.intake(changed, 12).code, ResultCode.CONFLICT)
        self.assertEqual(len(s.cases), 1)
        self.assertEqual(s.faults["SOURCE_COLLISION"], 1)

    def test_case_id_collision_is_rejected(self):
        s = build_component(ports())
        s.intake(payload(), 10)
        other = SealedIntake(payload().case_id, "ev_" + "f" * 32,
                             "f" * 64, CIPHER_SHA, "STANDARD", 2, 10)
        self.assertEqual(s.intake(other, 11).code, ResultCode.CONFLICT)

    def test_backpressure_never_claims_durable_receipt(self):
        s = build_component(ports(queue_cap=1))
        s.intake(payload(), 10)
        result = s.intake(payload(2), 10)
        self.assertEqual(result.code, ResultCode.BACKPRESSURE)
        self.assertEqual(result.reason, "RETAIN_SEALED_RECEIPT_AND_RETRY")
        self.assertNotIn(payload(2).case_id, s.cases)
        self.assertEqual(len(s.events), 1)

    def test_case_access_wrong_principal_denied(self):
        s = build_component(ports())
        s.intake(payload(), 10)
        self.assertEqual(s.case_status("ordinary-worker", payload().case_id, 11).code,
                         ResultCode.BLOCKED_PRIVATE_ACCESS)
        self.assertEqual(s.faults["CASE_ACCESS_DENIAL"], 1)

    def test_no_investigator_preserves_pending_and_alerts(self):
        s = build_component(ports())
        s.intake(payload(), 10)
        r = s.route("custodian", payload().case_id, (), 11)
        self.assertEqual(r.code, ResultCode.BLOCKED_INDEPENDENCE)
        self.assertEqual(s.cases[payload().case_id].state, "PENDING")
        self.assertTrue(any(a.kind == "NO_INVESTIGATOR" for a in s.alerts.values()))

    def test_independence_and_clearance_deny(self):
        s = build_component(ports())
        p = payload()
        s.intake(p, 10)
        invalid = Investigator("investigator", 1, True, frozenset({"STANDARD"}),
                               frozenset({p.subject_token}))
        self.assertEqual(s.route("custodian", p.case_id, (invalid,), 11).code,
                         ResultCode.BLOCKED_INDEPENDENCE)
        no_clearance = Investigator("investigator", 1, True, frozenset({"RESTRICTED"}))
        self.assertEqual(s.route("custodian", p.case_id, (no_clearance,), 11).code,
                         ResultCode.BLOCKED_INDEPENDENCE)

    def test_assignment_and_revocation(self):
        s = build_component(ports())
        p = payload()
        s.intake(p, 10)
        reviewer = Investigator("investigator", 1, True, frozenset({"STANDARD"}))
        self.assertEqual(s.route("custodian", p.case_id, (reviewer,), 20).code,
                         ResultCode.ACCEPTED)
        self.assertEqual(s.audit(21).investigator_coverage_pct, 100)
        self.assertEqual(s.revoke_investigator("custodian", p.case_id,
                                               "wrong", 22).code, ResultCode.DENIED)
        self.assertEqual(s.revoke_investigator("custodian", p.case_id,
                                               "investigator", 22).code, ResultCode.PENDING)
        self.assertEqual(s.cases[p.case_id].state, "PENDING")

    def test_investigator_capacity_denied(self):
        s = build_component(ports())
        p = payload()
        s.intake(p, 10)
        full = Investigator("investigator", 1, True, frozenset({"STANDARD"}),
                            load_limit=2, current_load=2)
        self.assertEqual(s.route("custodian", p.case_id, (full,), 15).code,
                         ResultCode.BLOCKED_INDEPENDENCE)

    def test_missing_key_preserves_case(self):
        s = build_component(ports(private_key_available=lambda: False))
        p = payload()
        s.intake(p, 10)
        reviewer = Investigator("investigator", 1, True, frozenset({"STANDARD"}))
        self.assertEqual(s.route("custodian", p.case_id, (reviewer,), 11).code,
                         ResultCode.PENDING)
        self.assertIsNone(s.cases[p.case_id].assigned_investigator)

    def test_audit_missed_and_overdue_no_witness(self):
        s = build_component(ports(check_interval_seconds=30))
        s.intake(payload(), 10)
        s.audit(15)
        report = s.audit(60)
        self.assertEqual(report.overdue_checks, 1)
        self.assertEqual(report.fault_counts["MISSED_AUDIT"], 1)
        self.assertGreater(report.oldest_case_age_seconds, 0)
        self.assertTrue(any(a.kind == "MISSED_AUDIT" for a in s.alerts.values()))

    def test_owner_delivery_unacknowledged_until_verified(self):
        deliveries = []
        s = build_component(ports(send_owner_alert=lambda *args: deliveries.append(args) or True,
                                  verify_alert_ack=lambda *args: False))
        s.intake(payload(severity=4), 10)
        self.assertEqual(s.deliver_alerts(11).code, ResultCode.PENDING)
        self.assertTrue(deliveries)
        first = next(iter(s.alerts))
        self.assertEqual(s.acknowledge_alert(first, "fake", 12).code, ResultCode.DENIED)
        self.assertEqual(s.alerts[first].state, "SENT_UNACKNOWLEDGED")

    def test_valid_synthetic_ack(self):
        s = build_component(ports(verify_alert_ack=lambda *args: True))
        s.intake(payload(severity=4), 10)
        first = next(iter(s.alerts))
        self.assertEqual(s.acknowledge_alert(first, "synthetic", 11).code, ResultCode.ACCEPTED)
        self.assertEqual(s.alerts[first].state, "ACKNOWLEDGED")

    def test_archive_rejects_plaintext_or_unverified_encryption_port(self):
        s = build_component(ports(verify_ciphertext_encryption=lambda *args: False))
        s.intake(payload(), 10)
        r = s.stage_archive("custodian", payload().case_id, CIPHERTEXT,
                            retention(), 12, "synthetic_sig")
        self.assertEqual(r.code, ResultCode.DENIED)
        self.assertFalse(s.archives)

    def test_archive_cipher_hash_mismatch_quarantines(self):
        s = build_component(ports(verify_ciphertext_encryption=lambda *args: True))
        s.intake(payload(), 10)
        r = s.stage_archive("custodian", payload().case_id, b"tampered",
                            retention(), 12, "synthetic_sig")
        self.assertEqual(r.code, ResultCode.QUARANTINED)
        self.assertFalse(s.archives)

    def test_archive_signature_missing_is_not_verified(self):
        d = ports()
        d.pop("verify_archive_signature")
        s = build_component(d)
        s.intake(payload(), 10)
        r = s.stage_archive("custodian", payload().case_id, CIPHERTEXT,
                            retention(), 12, "synthetic_sig")
        self.assertEqual(s.verify_archive("custodian", r.ref, 13).code,
                         ResultCode.NOT_VERIFIED)

    def test_restore_failure_quarantines_and_blocks_retention(self):
        s = build_component(ports())
        s.intake(payload(), 10)
        r = s.stage_archive("custodian", payload().case_id, CIPHERTEXT,
                            retention(), 12, "synthetic_sig")
        self.assertEqual(s.verify_archive("custodian", r.ref, 13).code, ResultCode.PENDING)
        manifest = s.archives[r.ref].manifest
        bad = RestoreReceipt(r.ref, manifest.manifest_sha256, "0" * 64,
                             "machine_witness", 1, True)
        self.assertEqual(s.verify_restore("custodian", bad, 14).code,
                         ResultCode.QUARANTINED)
        s.update_custody_hold("custodian", payload().case_id, False, 15,
                              lambda *args: True)
        self.assertEqual(s.authorize_retention_deletion("custodian", r.ref,
                         retention(), 200000, "owner", lambda *args: True).code,
                         ResultCode.DENIED)

    def test_verified_restore_still_does_not_delete_and_has_hold(self):
        s = build_component(ports())
        s.intake(payload(), 10)
        r = s.stage_archive("custodian", payload().case_id, CIPHERTEXT,
                            retention(), 12, "synthetic_sig")
        s.verify_archive("custodian", r.ref, 13)
        m = s.archives[r.ref].manifest
        receipt = RestoreReceipt(r.ref, m.manifest_sha256, m.ciphertext_sha256,
                                 "machine_witness", 2, True)
        self.assertEqual(s.verify_restore("custodian", receipt, 14).code, ResultCode.ACCEPTED)
        self.assertEqual(s.authorize_retention_deletion("custodian", r.ref,
                         retention(), 200000, "owner", lambda *args: True).code,
                         ResultCode.DENIED)
        self.assertIn(payload().case_id, s.cases)

    def test_only_verified_owner_can_approve_retention_eligibility(self):
        s = build_component(ports())
        s.intake(payload(), 10)
        r = s.stage_archive("custodian", payload().case_id, CIPHERTEXT,
                            retention(), 12, "synthetic_sig")
        s.verify_archive("custodian", r.ref, 13)
        m = s.archives[r.ref].manifest
        s.verify_restore("custodian", RestoreReceipt(r.ref, m.manifest_sha256,
                         m.ciphertext_sha256, "machine_witness", 2, True), 14)
        s.update_custody_hold("custodian", payload().case_id, False, 15,
                              lambda *args: True)
        self.assertEqual(s.authorize_retention_deletion("custodian", r.ref,
                         retention(), 200000, "fabricated", lambda *args: False).code,
                         ResultCode.DENIED)
        self.assertEqual(s.authorize_retention_deletion("custodian", r.ref,
                         retention(), 200000, "synthetic", lambda *args: True).code,
                         ResultCode.ACCEPTED)
        self.assertIn(payload().case_id, s.cases)

    def test_routine_message_never_triggers_p0_fleet_wide(self):
        s = build_component(ports())
        p = payload(severity=1)
        s.intake(p, 10)
        proof = P0Proof(p.case_id, "CREDENTIAL_ABUSE", "f" * 64,
                        "P0-test", "REQUEST_LEASE_FENCE", "FLEET",
                        "synthetic_owner", 30)
        self.assertEqual(s.request_p0_isolation("custodian", proof, 11).code,
                         ResultCode.DENIED)
        self.assertFalse(s._p0_replay)

    def test_verified_narrow_p0_is_advisory_and_replay_bounded(self):
        s = build_component(ports())
        p = payload(severity=4, trigger="CREDENTIAL_ABUSE")
        s.intake(p, 10)
        proof = P0Proof(p.case_id, "CREDENTIAL_ABUSE", "f" * 64,
                        "P0-test", "REQUEST_LEASE_FENCE", "JOB_LEASE",
                        "synthetic_owner", 100)
        r = s.request_p0_isolation("custodian", proof, 11)
        self.assertEqual(r.code, ResultCode.PENDING)
        self.assertEqual(r.reason, "EXTERNAL_GATEWAY_NOT_EXECUTED")
        self.assertEqual(s.request_p0_isolation("custodian", proof, 12).reason,
                         "IDEMPOTENT_REQUEST")
        changed = P0Proof(p.case_id, "CREDENTIAL_ABUSE", "e" * 64,
                          "P0-test", "REQUEST_LEASE_FENCE", "JOB_LEASE",
                          "synthetic_owner", 100)
        self.assertEqual(s.request_p0_isolation("custodian", changed, 13).code,
                         ResultCode.DENIED)

    def test_no_p0_privilege_without_ports(self):
        s = build_component(ports(verify_p0_policy_proof=None))
        p = payload(severity=4, trigger="SOURCE_TAMPERING")
        s.intake(p, 10)
        proof = P0Proof(p.case_id, "SOURCE_TAMPERING", "e" * 64,
                        "P0-test", "REQUEST_ACCESS_REVIEW", "WORKER_ID",
                        "synthetic_owner", 20)
        self.assertEqual(s.request_p0_isolation("custodian", proof, 11).code,
                         ResultCode.DENIED)

    def test_recommendation_not_enforcement(self):
        s = build_component(ports())
        s.intake(payload(), 10)
        r = s.recommend_restriction("custodian", payload().case_id,
                                    "PAUSE_NEW_ASSIGNMENTS", 11)
        self.assertEqual(r.code, ResultCode.PENDING)
        self.assertEqual(r.reason, "ADVISORY_ONLY_NO_ENFORCEMENT")
        self.assertEqual(s.recommend_restriction("custodian", payload().case_id,
                         "DELETE_WORKER", 11).code, ResultCode.DENIED)

    def test_fault_classification_and_no_inferred_human(self):
        s = build_component()
        s.host_status(False, 5)
        s.record_fault("RESTORE_FAILURE", 6)
        self.assertEqual(s.audit(7).fault_counts["HOST_OUTAGE"], 1)
        with self.assertRaises(ValueError):
            s.record_fault("human_saw_this", 10)

    def test_failing_port_produces_denial(self):
        def broken(*args):
            raise RuntimeError("no provider")
        s = build_component(ports(authorize_case=broken))
        s.intake(payload(), 10)
        self.assertEqual(s.case_status("custodian", payload().case_id, 11).code,
                         ResultCode.BLOCKED_PRIVATE_ACCESS)
        self.assertEqual(s.faults["PROVIDER_FAILURE"], 1)

    def test_no_provider_side_effect_implicit(self):
        calls = []
        s = build_component(ports(send_owner_alert=lambda *args: calls.append(args) or True))
        s.intake(payload(severity=4), 10)
        s.audit(11)
        s.recommend_restriction("custodian", payload().case_id, "FLAG_FOR_OWNER", 12)
        self.assertEqual(calls, [])  # only explicit deliver_alerts can invoke port


if __name__ == "__main__":
    unittest.main()

class ExtendedIntegrityTests(unittest.TestCase):
    def test_archive_manifest_digest_mutation_fails_closed(self):
        from dataclasses import replace
        s = build_component(ports())
        s.intake(payload(), 10)
        r = s.stage_archive("custodian", payload().case_id, CIPHERTEXT,
                            retention(), 12, "synthetic_sig")
        s.archives[r.ref].manifest = replace(s.archives[r.ref].manifest,
                                           manifest_sha256="0" * 64)
        self.assertEqual(s.verify_archive("custodian", r.ref, 13).code,
                         ResultCode.QUARANTINED)
        self.assertEqual(s.archive_publication_readiness("custodian", r.ref, 14).code,
                         ResultCode.QUARANTINED)

    def test_failed_restore_blocks_archive_publication(self):
        s = build_component(ports())
        s.intake(payload(), 10)
        r = s.stage_archive("custodian", payload().case_id, CIPHERTEXT,
                            retention(), 12, "synthetic_sig")
        self.assertEqual(s.archive_publication_readiness("custodian", r.ref, 13).code,
                         ResultCode.NOT_VERIFIED)
        s.verify_archive("custodian", r.ref, 14)
        m = s.archives[r.ref].manifest
        receipt = RestoreReceipt(r.ref, m.manifest_sha256, m.ciphertext_sha256,
                                 "machine_witness", 1, False)
        s.verify_restore("custodian", receipt, 15)
        self.assertEqual(s.archive_publication_readiness("custodian", r.ref, 16).code,
                         ResultCode.QUARANTINED)

    def test_restore_witness_is_not_staging_actor(self):
        s = build_component(ports(verify_restore_witness=lambda *args: True))
        s.intake(payload(), 10)
        r = s.stage_archive("custodian", payload().case_id, CIPHERTEXT,
                            retention(), 12, "synthetic_sig")
        s.verify_archive("custodian", r.ref, 13)
        m = s.archives[r.ref].manifest
        same_actor = RestoreReceipt(r.ref, m.manifest_sha256, m.ciphertext_sha256,
                                    "custodian", 1, True)
        self.assertEqual(s.verify_restore("custodian", same_actor, 14).code,
                         ResultCode.QUARANTINED)

    def test_queue_selects_urgent_and_age_avoids_starvation(self):
        s = build_component(ports())
        s.intake(payload(1, severity=1, received_at=1), 1)
        s.intake(payload(2, severity=4, received_at=800), 800)
        self.assertEqual(s.next_case("custodian", 801).ref, payload(2).case_id)
        self.assertEqual(s.next_case("custodian", 2000000).ref, payload(1).case_id)
        self.assertEqual(s.next_case("outsider", 801).code,
                         ResultCode.BLOCKED_PRIVATE_ACCESS)
