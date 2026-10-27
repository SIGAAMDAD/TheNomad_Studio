import datetime as dt
import json
import pathlib
import tempfile
import unittest
from scripts import studio_director_pulse as pulse


def write(root, path, data):
    p = pathlib.Path(root) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) + "\n")


def fixtures(root):
    write(root, "runtime/studio/jobs.json", {"jobs": [
        {"id": "FEATURE-6-G00", "approved": True, "state": "SUBMITTED"}]})
    write(root, "runtime/studio/slots.json", {"slots": [
        {"id": "S00", "enabled": True, "job": None}]})
    write(root, "runtime/studio/chat_workers.json", {"schema_version": 1, "workers": [
        {"worker_id": "CW-ENG-001", "status": "ASSIGNED",
         "job_id": "FEATURE-6-G00", "role_id": "rpg-ai-programmer",
         "assignment_epoch": 1, "audit_interval_minutes": 60}]})


class DirectorPulseTests(unittest.TestCase):
    def test_missing_data_is_explicit_and_report_is_immutable(self):
        with tempfile.TemporaryDirectory() as root:
            fixtures(root)
            report = pulse.build(pathlib.Path(root)/"runtime", pathlib.Path(root)/"comms",
                when=dt.datetime(2026, 10, 27, 18, 30, tzinfo=dt.timezone.utc))
            self.assertEqual(report["registered_chat_workers"], 1)
            self.assertIn("INBOXES_MISSING", report["data_gaps"])
            self.assertFalse(report["oversight_case_details_included"])
            self.assertFalse(report["chat_workers"][0]["chat_execution_verified"])
            self.assertTrue(pulse.write_report(pathlib.Path(root)/"comms", report)[0])
            self.assertFalse(pulse.write_report(pathlib.Path(root)/"comms", report)[0])

    def test_timezone_cst_and_cdt(self):
        with tempfile.TemporaryDirectory() as root:
            fixtures(root)
            summer = pulse.build(pathlib.Path(root)/"runtime", pathlib.Path(root)/"comms",
                when=dt.datetime(2026,6,1,4,0,tzinfo=dt.timezone.utc),kind="daily")
            winter = pulse.build(pathlib.Path(root)/"runtime", pathlib.Path(root)/"comms",
                when=dt.datetime(2026,12,1,5,0,tzinfo=dt.timezone.utc),kind="daily")
            self.assertEqual(summer["date_central"], "2026-05-31")
            self.assertEqual(winter["date_central"], "2026-11-30")

    def test_ci_cannot_approve_qa(self):
        with tempfile.TemporaryDirectory() as root:
            fixtures(root)
            write(root, "comms/evidence/source_ci/TheNomad_GOAP/ci-123.json", {
                "schema_version": 1, "receipt_id": "ci-123",
                "job_id": "FEATURE-6-G00", "source_run_metadata_verified": True,
                "runtime_promotion_authorized": False, "run_conclusion": "success",
                "source_sha": "a"*40, "source_repo": "SIGAAMDAD/TheNomad_GOAP",
                "artifacts_content_verified": False})
            report = pulse.build(pathlib.Path(root)/"runtime", pathlib.Path(root)/"comms")
            self.assertEqual(report["source_ci_jobs_with_evidence"],1)
            self.assertEqual(report["chat_workers"][0]["source_ci"]["independent_qa"], "NOT_ESTABLISHED")

    def test_invalid_ci_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            fixtures(root)
            write(root,"comms/evidence/source_ci/TheNomad_GOAP/ci-fake.json",{
                "receipt_id":"ci-fake","source_run_metadata_verified":False,
                "runtime_promotion_authorized":False})
            with self.assertRaisesRegex(pulse.PulseError,"UNVERIFIED_CI_RECEIPT"):
                pulse.build(pathlib.Path(root)/"runtime", pathlib.Path(root)/"comms")


if __name__ == "__main__":
    unittest.main()
