import datetime as dt
import json
import pathlib
import tempfile
import unittest
from scripts import studio_worker_health as health

NOW=dt.datetime(2026,10,27,22,0,tzinfo=dt.timezone.utc)
ID="CW-ENG-001"
JOB="FEATURE-6-G00"
REG={ID:{"status":"ASSIGNED","job_id":JOB,"assignment_epoch":2,"audit_interval_minutes":30}}
JOBS={JOB:{"id":JOB,"state":"RUNNING"}}

def put(root,path,data):
    p=pathlib.Path(root)/path
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data),encoding="utf-8")

def msg(kind,minute,attempt="CW-ENG-001-A"):
    return {"event_id":"evt-"+f"{minute:032x}","delivery_authority":"ARCHIVE_ONLY",
            "worker_id":ID,"job_id":JOB,"assignment_epoch":2,"event_type":kind,
            "attempt_id":attempt,"ledger_event_id":"LEDGER-001",
            "recorded_at_utc":(NOW+dt.timedelta(minutes=minute)).isoformat()}
def led(todos=None):
    return {"schema_version":4,"event_id":"LEDGER-001",
            "worker_slot":ID,"job_id":JOB,"attempt_id":"CW-ENG-001-A","todo":todos or []}

class WorkerHealthTests(unittest.TestCase):
    def test_unobserved_assignment(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("WORKER_NOT_OBSERVED",health.audit(d,d,REG,JOBS,NOW)["alerts"])
    def test_yield_without_ledger_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            for kind,m in (("STARTED",1),("YIELDED",2)):
                put(d,"events/2026/10/27/evt-"+f"{m:032x}"+".json",msg(kind,m))
            out=health.audit(d,d,REG,JOBS,NOW+dt.timedelta(minutes=20))
            self.assertIn("LEDGER_EVENT_MISSING",out["alerts"])
            self.assertIn("YIELD_WITHOUT_CHECKPOINT",out["alerts"])
    def test_valid_ledger_closes_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            for kind,m in (("STARTED",1),("SUBMITTED",2)):
                put(d,"events/2026/10/27/evt-"+f"{m:032x}"+".json",msg(kind,m))
            put(d,"studio/ledgers/events/engineering/"+JOB+"/LEDGER-001.json",led())
            out=health.audit(d,d,REG,JOBS,NOW+dt.timedelta(minutes=20))
            self.assertEqual(out["alerts"],{})
    def test_submitted_with_open_todo_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            for kind,m in (("STARTED",1),("SUBMITTED",2)):
                put(d,"events/2026/10/27/evt-"+f"{m:032x}"+".json",msg(kind,m))
            put(d,"studio/ledgers/events/engineering/"+JOB+"/LEDGER-001.json",
                led([{"id":"x","state":"OPEN"}]))
            out=health.audit(d,d,REG,JOBS,NOW+dt.timedelta(minutes=20))
            self.assertIn("TERMINAL_WITH_OPEN_TODOS",out["alerts"])
    def test_inbox_ack_overdue(self):
        with tempfile.TemporaryDirectory() as d:
            put(d,"inboxes/current/"+ID+".json",
                {"worker_id":ID,"authoritative":False,"messages":[
                  {"requires_ack":True,"ack_event_id":None,
                   "timestamp_utc":(NOW-dt.timedelta(hours=2)).isoformat()}]})
            out=health.audit(d,d,REG,JOBS,NOW)
            self.assertIn("INBOX_ACK_OVERDUE",out["alerts"])
if __name__=="__main__":unittest.main()
