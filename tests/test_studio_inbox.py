import json
import pathlib
import tempfile
import unittest

from scripts import studio_comms as c
from scripts import studio_inbox as inbox


def runtime_jobs():
    return c.approved_jobs({"jobs": [
        {"id": "FEATURE-6-G00", "approved": True, "state": "READY",
         "parent_feature": "FEATURE-6", "role": "rpg-ai-programmer",
         "repository": "SIGAAMDAD/TheNomad_GOAP"},
        {"id": "QA-FEATURE-6-G00", "approved": True, "state": "READY",
         "subject_job": "FEATURE-6-G00", "kind": "review",
         "role": "rpg-technical-qa-analyst",
         "repository": "SIGAAMDAD/TheNomad_GOAP"},
    ]})


def registry():
    return c.chat_registry({"schema_version": 1, "workers": [
        {"worker_id": "CW-ENG-001", "status": "ASSIGNED",
         "job_id": "FEATURE-6-G00", "role_id": "rpg-ai-programmer",
         "assignment_epoch": 1, "audit_interval_minutes": 60},
        {"worker_id": "CW-QA-002", "status": "ASSIGNED",
         "job_id": "QA-FEATURE-6-G00", "role_id": "rpg-technical-qa-analyst",
         "assignment_epoch": 2, "audit_interval_minutes": 60},
    ]})


def candidate(jid, worker, kind, recipient_job, recipient_worker=None,
              causation=None, ack=True):
    return {
        "schema_version": 1, "job_id": jid, "worker_id": worker,
        "event_type": kind, "recipient_department": "qa",
        "recipient_job_id": recipient_job, "recipient_worker_id": recipient_worker,
        "summary": "Synthetic handoff; no source work performed",
        "requires_ack": ack, "requested_action": "ACKNOWLEDGE",
        "causation_id": causation,
    }


def record(wire, number):
    body = chr(96)*3 + "studio-message\n" + json.dumps(wire) + "\n" + chr(96)*3
    fake = {"id": number, "created_at": "2026-10-27T18:15:00Z",
            "user": {"login": "SIGAAMDAD"}, "body": body}
    # registry=None here isolates normalized message processing; the full
    # registered-identity/authorization flow is separately checked below.
    return c.normalize_comment(fake, 6, runtime_jobs())


class InboxTests(unittest.TestCase):
    def test_handoff_to_ack_replay_idempotent(self):
        jobs, workers = runtime_jobs(), registry()
        first = record(candidate("FEATURE-6-G00", "CW-ENG-001", "HANDOFF_READY",
                                 "QA-FEATURE-6-G00", "CW-QA-002"), 101)
        with tempfile.TemporaryDirectory() as path:
            c.write_event(path, first)
            result = inbox.materialize(path, workers, jobs)
            self.assertEqual(result["registered_inboxes"], 2)
            receiver = json.loads((pathlib.Path(path) / "inboxes/current/CW-QA-002.json").read_text())
            self.assertEqual(receiver["pending"], 1)
            self.assertEqual(receiver["messages"][0]["event_id"], first["event_id"])
            self.assertEqual(inbox.materialize(path, workers, jobs)["projection_files_changed"], 0)

            ack = record(candidate("QA-FEATURE-6-G00", "CW-QA-002", "ACK",
                                   "FEATURE-6-G00", "CW-ENG-001",
                                   causation=first["event_id"], ack=False), 102)
            c.write_event(path, ack)
            inbox.materialize(path, workers, jobs)
            receiver = json.loads((pathlib.Path(path) / "inboxes/current/CW-QA-002.json").read_text())
            self.assertEqual(receiver["pending"], 0)
            self.assertEqual(receiver["acknowledged"], 1)
            self.assertEqual(receiver["messages"][0]["ack_event_id"], ack["event_id"])
            self.assertEqual(inbox.materialize(path, workers, jobs)["projection_files_changed"], 0)

    def test_wrong_worker_cannot_ack(self):
        workers, jobs = registry(), runtime_jobs()
        first = record(candidate("FEATURE-6-G00", "CW-ENG-001", "QA_REQUEST",
                                 "QA-FEATURE-6-G00", "CW-QA-002"), 201)
        # This ACK claims that sender may acknowledge their own request.
        bad = record(candidate("FEATURE-6-G00", "CW-ENG-001", "ACK",
                               "FEATURE-6-G00", causation=first["event_id"],
                               ack=False), 202)
        result, _, invalid = inbox.build([first, bad], workers, jobs)
        self.assertIn(bad["event_id"], invalid)
        self.assertEqual(result["CW-QA-002"]["pending"], 1)

    def test_unassigned_message_kept_for_later(self):
        workers = registry()
        workers["CW-QA-002"]["status"] = "PAUSED"
        event = record(candidate("FEATURE-6-G00", "CW-ENG-001", "QA_REQUEST",
                                 "QA-FEATURE-6-G00", "CW-QA-002"), 301)
        results, waiting, invalid = inbox.build([event], workers, runtime_jobs())
        self.assertEqual(results["CW-QA-002"]["pending"], 0)
        self.assertEqual(len(waiting["QA-FEATURE-6-G00"]), 1)
        self.assertEqual(invalid, [])

    def test_cannot_ack_orphan(self):
        ack = record(candidate("QA-FEATURE-6-G00", "CW-QA-002", "ACK",
                               "FEATURE-6-G00", causation="evt-"+("a"*32),
                               ack=False), 401)
        views, _, invalid = inbox.build([ack], registry(), runtime_jobs())
        self.assertEqual(len(invalid), 1)
        self.assertEqual(views["CW-QA-002"]["pending"], 0)

    def test_tampered_archive_refused(self):
        first = record(candidate("FEATURE-6-G00", "CW-ENG-001", "HANDOFF_READY",
                                 "QA-FEATURE-6-G00"), 501)
        with tempfile.TemporaryDirectory() as path:
            c.write_event(path, first)
            item = next((pathlib.Path(path) / "events").rglob("*.json"))
            tampered = json.loads(item.read_text())
            tampered["delivery_authority"] = "APPROVED_WRITE"
            item.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(inbox.InboxError, "INVALID_ARCHIVED_EVENT"):
                inbox.archive_events(path)

    def test_reassignment_erases_stale_derived_views(self):
        workers, jobs = registry(), runtime_jobs()
        event = record(candidate("FEATURE-6-G00", "CW-ENG-001", "QA_REQUEST",
                                 "QA-FEATURE-6-G00"), 601)
        with tempfile.TemporaryDirectory() as path:
            c.write_event(path, event)
            inbox.materialize(path, workers, jobs)
            prior = pathlib.Path(path) / "inboxes/current/CW-QA-002.json"
            self.assertTrue(prior.exists())
            workers.pop("CW-QA-002")
            inbox.materialize(path, workers, jobs)
            self.assertFalse(prior.exists())
            self.assertTrue((pathlib.Path(path) / "inboxes/unassigned/QA-FEATURE-6-G00.json").exists())

    def test_explicit_recipient_not_allowed_to_target_other_job(self):
        wire = candidate("FEATURE-6-G00", "CW-ENG-001", "HANDOFF_READY",
                         "QA-FEATURE-6-G00", "CW-ENG-001")
        body = chr(96)*3 + "studio-message\n" + json.dumps(wire) + "\n" + chr(96)*3
        with self.assertRaisesRegex(c.MessageError, "INVALID_RECIPIENT_WORKER"):
            c.normalize_comment({"id": 888, "created_at": "2026-10-27T18:15:00Z",
                                "user": {"login": "SIGAAMDAD"}, "body": body},
                                6, runtime_jobs(), registry=registry())


if __name__ == "__main__":
    unittest.main()
