import json
import pathlib
import tempfile
import unittest
from scripts import studio_comms as c


def jobs():
    return c.approved_jobs({"jobs": [
        {"id": "FEATURE-6-G00", "approved": True, "parent_feature": "FEATURE-6",
         "state": "READY", "repository": "SIGAAMDAD/TheNomad_GOAP",
         "role": "rpg-ai-programmer"},
        {"id": "FEATURE-6-G01", "approved": True, "parent_feature": "FEATURE-6",
         "state": "READY", "repository": "SIGAAMDAD/TheNomad_GOAP",
         "role": "rpg-ai-programmer"},
        {"id": "FEATURE-7-G00", "approved": True, "parent_feature": "FEATURE-7",
         "state": "READY", "repository": "SIGAAMDAD/Terrain3D",
         "role": "rpg-rendering-programmer"},
        {"id": "FEATURE-8-G00", "approved": False, "parent_feature": "FEATURE-8",
         "state": "READY", "repository": "SIGAAMDAD/Terrain3D",
         "role": "rpg-rendering-programmer"},
    ]})


def candidate():
    return {"schema_version": 1, "job_id": "FEATURE-6-G00", "worker_id": "CW-ENG-001",
            "event_type": "DEPENDENCY_REQUEST",
            "recipient_department": "engineering", "recipient_job_id": "FEATURE-6-G01",
            "summary": "Need frozen ABI specification",
            "requested_action": "PROVIDE_DEPENDENCY", "requires_ack": True}


def comment(wire=None, ident=101, actor="SIGAAMDAD"):
    body = "" if wire is None else chr(96)*3 + "studio-message\n" + json.dumps(wire) + "\n" + chr(96)*3
    return {"id": ident, "created_at": "2026-10-27T18:15:00Z",
            "user": {"login": actor}, "body": body}


class CommsTests(unittest.TestCase):
    def setUp(self):
        self.jobs = jobs()
        self.payload = candidate()

    def normalize(self, issue=6, **kwargs):
        return c.normalize_comment(comment(self.payload, **kwargs), issue, self.jobs)

    def test_archive_idempotent_and_digest(self):
        record = self.normalize()
        self.assertTrue(record["event_id"].startswith("evt-"))
        self.assertEqual(record["delivery_authority"], "ARCHIVE_ONLY")
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(c.write_event(folder, record))
            self.assertFalse(c.write_event(folder, record))
            self.assertEqual(c.audit(folder, self.jobs)["events"], 1)
            output, count = c.digest_day(folder, "2026-10-27")
            self.assertEqual(count, 1)
            self.assertEqual(c.digest_day(folder, "2026-10-27")[0], output)
            self.assertIn("CW-ENG-001", pathlib.Path(output).read_text())

    def test_sender_not_authorized(self):
        with self.assertRaisesRegex(c.MessageError, "UNAUTHORIZED_GITHUB_ACTOR"):
            self.normalize(actor="some-else")

    def test_wrong_issue(self):
        with self.assertRaisesRegex(c.MessageError, "COMMENT_ISSUE_JOB_MISMATCH"):
            self.normalize(issue=7)

    def test_cross_feature(self):
        self.payload["recipient_job_id"] = "FEATURE-7-G00"
        with self.assertRaisesRegex(c.MessageError, "UNAUTHORIZED_CROSS_JOB_MESSAGE"):
            self.normalize()

    def test_unapproved_job(self):
        self.payload["job_id"] = "FEATURE-8-G00"
        with self.assertRaisesRegex(c.MessageError, "JOB_NOT_APPROVED_OR_ACTIVE"):
            self.normalize(issue=8)

    def test_wrong_source_repo(self):
        self.payload["source_ref"] = {"repo": "SIGAAMDAD/Terrain3D", "ref": "main", "sha": "a"*40}
        with self.assertRaisesRegex(c.MessageError, "INVALID_SOURCE_REF"):
            self.normalize()

    def test_unverified_source_never_passes(self):
        self.payload["source_ref"] = {
            "repo": "SIGAAMDAD/TheNomad_GOAP", "ref": "main", "sha": "a"*40}
        self.assertEqual(self.normalize()["source_verification"], "NOT_RUN")

    def test_injected_extra_authority_rejected(self):
        self.payload["enable_source_writes"] = True
        with self.assertRaisesRegex(c.MessageError, "UNSUPPORTED_MESSAGE_FIELDS"):
            c.parse_wire(comment(self.payload)["body"])

    def test_duplicate_json_keys(self):
        raw = (chr(96)*3 + "studio-message\n" +
               '{"schema_version":1,"job_id":"FEATURE-6-G00","job_id":"FEATURE-6-G01"}' +
               "\n" + chr(96)*3)
        with self.assertRaises(c.MessageError):
            c.parse_wire(raw)

    def test_ack_requires_causation_and_no_ack_loop(self):
        self.payload["event_type"] = "ACK"
        with self.assertRaisesRegex(c.MessageError, "ACK_REQUIRES_CAUSATION"):
            self.normalize()
        self.payload["causation_id"] = "evt-" + "a"*32
        self.payload["requires_ack"] = False
        self.assertEqual(self.normalize()["event_type"], "ACK")

    def test_stable_source_identity(self):
        self.assertNotEqual(self.normalize(ident=101)["event_id"],
                            self.normalize(ident=102)["event_id"])

    def test_non_messages_and_limits(self):
        with self.assertRaisesRegex(c.MessageError, "NOT_STUDIO_MESSAGE"):
            c.parse_wire("Unrelated discussion.")
        with self.assertRaisesRegex(c.MessageError, "MESSAGE_TOO_LARGE"):
            c.parse_wire("x"*8193)

    def test_invalid_evidence(self):
        self.payload["evidence_refs"] = ["file:///etc/shadow"]
        with self.assertRaisesRegex(c.MessageError, "INVALID_EVIDENCE_REFS"):
            self.normalize()

    def test_issue_discovery(self):
        self.assertEqual(c.issue_for_job(self.jobs["FEATURE-6-G00"]), {6})

    def test_registered_chat_worker_binding(self):
        registry = c.chat_registry({"schema_version": 1, "workers": [{
            "worker_id": "CW-ENG-001", "job_id": "FEATURE-6-G00",
            "role_id": "rpg-ai-programmer", "status": "ASSIGNED",
            "assignment_epoch": 9, "audit_interval_minutes": 60,
        }]})
        self.payload["assignment_epoch"] = 9
        self.payload["role_id"] = "rpg-ai-programmer"
        record = c.normalize_comment(comment(self.payload), 6, self.jobs, registry=registry)
        self.assertEqual(record["identity_assurance"], "REGISTRY_MATCHED_SHARED_CONNECTOR")
        with tempfile.TemporaryDirectory() as folder:
            c.write_event(folder, record)
            audited = c.audit(folder, self.jobs, registry)
            self.assertEqual(audited["observed_workers"], 1)
            self.assertIn("audit_health", audited["last_observed"]["CW-ENG-001"])
        self.payload["assignment_epoch"] = 8
        with self.assertRaisesRegex(c.MessageError, "CHAT_WORKER_ASSIGNMENT_MISMATCH"):
            c.normalize_comment(comment(self.payload), 6, self.jobs, registry=registry)

    def test_unregistered_worker_denied(self):
        registry = c.chat_registry({"schema_version": 1, "workers": []})
        with self.assertRaisesRegex(c.MessageError, "CHAT_WORKER_UNREGISTERED"):
            c.normalize_comment(comment(self.payload), 6, self.jobs, registry=registry)

    def test_registered_worker_not_chat_authentication(self):
        registry = c.chat_registry({"schema_version": 1, "workers": [{
            "worker_id": "CW-ENG-001", "job_id": "FEATURE-6-G00",
            "role_id": "rpg-ai-programmer", "status": "ASSIGNED",
            "assignment_epoch": 1, "audit_interval_minutes": 60,
        }]})
        self.payload["assignment_epoch"] = 1
        record = c.normalize_comment(comment(self.payload), 6, self.jobs, registry=registry)
        self.assertEqual(record["delivery_authority"], "ARCHIVE_ONLY")
        self.assertNotEqual(record["identity_assurance"], "INDIVIDUALLY_AUTHENTICATED")



if __name__ == "__main__":
    unittest.main()
