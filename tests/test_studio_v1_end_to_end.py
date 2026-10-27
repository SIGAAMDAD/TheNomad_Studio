import datetime as dt
import json
import pathlib
import tempfile
import unittest
from scripts import studio_comms as comms, studio_inbox as inbox, studio_worker_health as health

NOW=dt.datetime(2026,10,27,22,0,tzinfo=dt.timezone.utc)
JOB="FEATURE-6-E2E"

def write(root,path,data):
    p=pathlib.Path(root)/path
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data))

def comment(kind,sender,num,minutes=0,**fields):
    data={"schema_version":1,"job_id":JOB,"worker_id":sender,
          "assignment_epoch":1,"role_id":"rpg-ai-programmer",
          "event_type":kind,"recipient_department":"engineering",
          "recipient_job_id":JOB,"summary":"Deterministic routing test",
          "attempt_id":sender+"-A",**fields}
    return {"id":num,"created_at":(NOW+dt.timedelta(minutes=minutes)).isoformat(),
            "user":{"login":"SIGAAMDAD"},
            "body":"\x60\x60\x60studio-message\n"+json.dumps(data)+"\n\x60\x60\x60"}

class V1EndToEndTests(unittest.TestCase):
    def test_hundred_worker_registry_immutable_relay_ack_and_ledger(self):
        raw=[{"worker_id":f"CW-ENG-{n:03}","status":"ASSIGNED",
              "department_id":"engineering","role_id":"rpg-ai-programmer",
              "job_id":JOB,"assignment_epoch":1,
              "audit_interval_minutes":60} for n in range(1,101)]
        registry=comms.chat_registry({"schema_version":1,"workers":raw})
        jobs=comms.approved_jobs({"jobs":[{"id":JOB,"approved":True,"state":"RUNNING",
                                          "role":"rpg-ai-programmer","parent_feature":"FEATURE-6"}]})
        sender="CW-ENG-001";recipient="CW-ENG-002"
        with tempfile.TemporaryDirectory() as root:
            root=pathlib.Path(root)
            msg=comment("HANDOFF_READY",sender,9001,recipient_worker_id=recipient,
                        requires_ack=True)
            first=comms.normalize_comment(msg,6,jobs,registry=registry)
            self.assertTrue(comms.write_event(root,first))
            self.assertFalse(comms.write_event(root,first))
            a=inbox.materialize(root,registry,jobs)
            self.assertEqual(a["registered_inboxes"],100)
            self.assertEqual(json.loads((root/"inboxes/current"/(recipient+".json")).read_text())["pending"],1)
            ack=comment("ACK",recipient,9002,minutes=1,recipient_worker_id=sender,
                        causation_id=first["event_id"],requires_ack=False)
            self.assertTrue(comms.write_event(root,comms.normalize_comment(ack,6,jobs,registry=registry)))
            inbox.materialize(root,registry,jobs)
            self.assertEqual(json.loads((root/"inboxes/current"/(recipient+".json")).read_text())["pending"],0)
            started=comment("STARTED",recipient,9003,minutes=2)
            submitted=comment("SUBMITTED",recipient,9004,minutes=4,
                              ledger_event_id="CW-ENG-002-LEDGER",elapsed_seconds=120,
                              resume_required=False)
            for item in (started,submitted):
                comms.write_event(root,comms.normalize_comment(item,6,jobs,registry=registry))
            write(root,"runtime/studio/ledgers/events/engineering/"+JOB+"/CW-ENG-002-LEDGER.json",{
                "schema_version":4,"event_id":"CW-ENG-002-LEDGER",
                "recorded_at_utc":(NOW+dt.timedelta(minutes=4)).isoformat(),
                "request_id":"FEATURE-6","department_id":"engineering",
                "role_id":"rpg-ai-programmer","mode":"interactive",
                "event_type":"ARTIFACT_SUBMITTED","status":"SUBMITTED",
                "summary":"Synthetic independent plumbing test",
                "issue":{"repo":"SIGAAMDAD/TheNomad_Studio","number":6,
                         "url":"https://github.com/SIGAAMDAD/TheNomad_Studio/issues/6"},
                "job_id":JOB,"attempt_id":recipient+"-A","worker_slot":recipient,
                "todo":[{"id":"T-1","description":"ACK test fixture","state":"DONE"}],
                "dependencies":[],"artifacts":[],"checks":[],"owner_decisions":[]})
            output=health.audit(root/"runtime",root,registry,jobs,NOW+dt.timedelta(minutes=20))
            self.assertEqual(output["registered"],100)
            self.assertEqual([w for w in output["workers"] if w["worker_id"]==recipient][0]["alerts"],[])
            self.assertEqual(sum(w["ledger_entries"] for w in output["workers"]),1)
            self.assertEqual(output["alerts"].get("WORKER_NOT_OBSERVED"),98)
if __name__=="__main__":unittest.main()
