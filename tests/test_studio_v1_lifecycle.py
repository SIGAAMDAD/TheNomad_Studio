import json
import unittest
from scripts import studio_comms as c

def wire(kind):
    return {"schema_version":1,"job_id":"FEATURE-6-G00","worker_id":"CW-ENG-001",
            "event_type":kind,"recipient_department":"engineering",
            "summary":"Lifecycle event with actual observed evidence",
            "assignment_epoch":3,"attempt_id":"CW-ENG-001-A",
            "role_id":"rpg-ai-programmer"}

def jobs():
    return c.approved_jobs({"jobs":[{"id":"FEATURE-6-G00","approved":True,
        "state":"RUNNING","parent_feature":"FEATURE-6","role":"rpg-ai-programmer"}]})

def comment(w):
    return {"id":55,"created_at":"2026-10-27T22:00:00Z",
        "user":{"login":"SIGAAMDAD"},
        "body":"\x60\x60\x60studio-message\n"+json.dumps(w)+"\n\x60\x60\x60"}

class LifecycleTests(unittest.TestCase):
    def test_started_requires_attempt_and_epoch(self):
        w=wire("STARTED")
        self.assertEqual(c.normalize_comment(comment(w),6,jobs())["event_type"],"STARTED")
        del w["attempt_id"]
        with self.assertRaisesRegex(c.MessageError,"LIFECYCLE_ATTEMPT_REQUIRED"):
            c.normalize_comment(comment(w),6,jobs())
    def test_yield_requires_durable_checkpoint(self):
        w=wire("YIELDED")
        with self.assertRaisesRegex(c.MessageError,"LIFECYCLE_LEDGER_REQUIRED"):
            c.normalize_comment(comment(w),6,jobs())
        w.update(ledger_event_id="LEDGER-001",elapsed_seconds=900,resume_required=True)
        x=c.normalize_comment(comment(w),6,jobs())
        self.assertEqual(x["ledger_event_id"],"LEDGER-001")
        self.assertTrue(x["resume_required"])
    def test_yield_without_continuation_rejected(self):
        w=wire("YIELDED")
        w.update(ledger_event_id="LEDGER-001",elapsed_seconds=300,resume_required=False)
        with self.assertRaisesRegex(c.MessageError,"YIELD_REQUIRES_CONTINUATION"):
            c.normalize_comment(comment(w),6,jobs())
    def test_submitted_tracks_elapsed_seconds(self):
        w=wire("SUBMITTED")
        w.update(ledger_event_id="LEDGER-001",elapsed_seconds=4,resume_required=False)
        self.assertEqual(c.normalize_comment(comment(w),6,jobs())["elapsed_seconds"],4)
if __name__=="__main__":unittest.main()
