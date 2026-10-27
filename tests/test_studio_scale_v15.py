"""V1.5 synthetic load/negative tests. No live AI or source writes."""
import json
import pathlib
import unittest
from scripts import studio_comms as comms, studio_inbox as inbox

def workers(n):
    return comms.chat_registry({"schema_version":1,"workers":[
        {"worker_id":f"CW-ENG-{i:04}","status":"ASSIGNED",
         "department_id":"engineering","role_id":"rpg-ai-programmer",
         "job_id":"FEATURE-500","assignment_epoch":1,
         "audit_interval_minutes":60} for i in range(n)]})

def event(i,kind="HEARTBEAT",target=None,ack=False):
    return {"schema_version":1,"event_id":f"evt-{i:032x}",
        "recorded_at_utc":"2026-10-27T21:00:00Z",
        "event_type":kind,"job_id":"FEATURE-500",
        "worker_id":f"CW-ENG-{i%500:04}","assignment_epoch":1,
        "identity_assurance":"REGISTRY_MATCHED_SHARED_CONNECTOR",
        "delivery_authority":"ARCHIVE_ONLY",
        "recipient":{"department_id":"engineering",
                     "job_id":"FEATURE-500","worker_id":target},
        "summary":"Synthetic lifecycle/load fixture",
        "requested_action":"NONE","requires_ack":ack,
        "source_verification":"NOT_RUN","evidence_refs":[]}

class ScaleTest(unittest.TestCase):
    def test_500_registration_and_guard(self):
        self.assertEqual(len(workers(500)),500)
        with self.assertRaisesRegex(comms.MessageError,"INVALID_CHAT_REGISTRY"):
            workers(1025)
        schema=json.loads((pathlib.Path(__file__).parents[1]/
            "studio/communications/schemas/chat-worker-registry.v1.schema.json").read_text())
        self.assertGreaterEqual(schema["properties"]["workers"]["maxItems"],500)

    def test_telemetry_does_not_flood_peer_inboxes(self):
        registry=workers(500)
        messages=[event(i) for i in range(10000)]
        result,waiting,invalid=inbox.build(messages,registry,{"FEATURE-500":{}})
        self.assertEqual(sum(len(x["messages"]) for x in result.values()),0)
        self.assertEqual(waiting,{})
        self.assertEqual(invalid,[])
        self.assertEqual(len(result),500)

    def test_actionable_broadcast_must_be_bounded(self):
        registry=workers(500)
        with self.assertRaisesRegex(inbox.InboxError,"BROADCAST_FANOUT_EXCEEDED"):
            inbox.build([event(1,"HANDOFF_READY")],registry,{"FEATURE-500":{}})

    def test_explicit_directed_handoff_and_ack(self):
        registry=workers(500)
        x=event(1,"HANDOFF_READY",target="CW-ENG-0002",ack=True)
        receiver=event(501,"ACK",target="CW-ENG-0001")
        receiver["worker_id"]="CW-ENG-0002"
        receiver["job_id"]="FEATURE-500"
        receiver["causation_id"]=x["event_id"]
        result,waiting,invalid=inbox.build([x,receiver],registry,{"FEATURE-500":{}})
        self.assertEqual(result["CW-ENG-0002"]["acknowledged"],1)
        self.assertEqual(result["CW-ENG-0002"]["pending"],0)
        self.assertEqual(result["CW-ENG-0001"]["pending"],0)
        self.assertEqual(waiting,{})
        self.assertEqual(invalid,[])

    def test_broker_rejects_unaddressed_telemetry_ack(self):
        doc=comms.approved_jobs({"jobs":[{
            "id":"FEATURE-500","approved":True,"parent_feature":"FEATURE-500",
            "state":"READY","role":"rpg-ai-programmer"}]})
        raw={"schema_version":1,"job_id":"FEATURE-500",
             "worker_id":"CW-ENG-0001","event_type":"HEARTBEAT",
             "recipient_department":"engineering",
             "summary":"Synthetic synthetic heartbeat",
             "requires_ack":True,"assignment_epoch":1,
             "attempt_id":"ATTEMPT-500"}
        with self.assertRaisesRegex(comms.MessageError,"TELEMETRY_ACK_REQUIRES"):
            comms.validate_wire(raw,doc,500)

if __name__=="__main__":
    unittest.main()
