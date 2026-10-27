#!/usr/bin/env python3
"""Synthetic in-memory coordination projector microbenchmark. No model/GitHub writes."""
import argparse
import collections
import json
import time
from scripts import studio_comms as comms, studio_inbox as inbox

def run(workers,events):
    active=comms.chat_registry({"schema_version":1,"workers":[
        {"worker_id":f"CW-ENG-{i:04}","status":"ASSIGNED",
         "department_id":"engineering","role_id":"rpg-ai-programmer",
         "job_id":f"FEATURE-{i//10:04}","assignment_epoch":1,
         "audit_interval_minutes":60} for i in range(workers)]})
    docs={f"FEATURE-{i//10:04}":{} for i in range(workers)}
    payloads=[]
    for i in range(events):
        owner=i%workers
        job=f"FEATURE-{owner//10:04}"
        payloads.append({
            "event_id":f"evt-{i:032x}","event_type":"HEARTBEAT",
            "recorded_at_utc":"2026-10-27T21:00:00Z",
            "job_id":job,"worker_id":f"CW-ENG-{owner:04}",
            "recipient":{"job_id":job,"worker_id":None,
                         "department_id":"engineering"},
            "summary":"Synthetic observation","requires_ack":False,
            "delivery_authority":"ARCHIVE_ONLY"})
    started=time.perf_counter()
    inboxes,waiting,invalid=inbox.build(payloads,active,docs)
    duration=time.perf_counter()-started
    return {"synthetic_only":True,"registered":workers,
            "events":events,"inbox_entries":sum(len(v["messages"]) for v in inboxes.values()),
            "waiting_jobs":len(waiting),"invalid_acks":len(invalid),
            "projector_seconds":round(duration,6),
            "events_per_second_in_memory":round(events/max(duration,0.000001),1),
            "not_measured":"GitHub Action startup, API limits, git commits, external queue latency, real ChatGPT concurrency"}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--workers",type=int,default=500)
    ap.add_argument("--events",type=int,default=10000)
    a=ap.parse_args()
    if not 1<=a.workers<=1024 or not 1<=a.events<=100000:
        ap.error("worker/event limits exceeded")
    print(json.dumps(run(a.workers,a.events),sort_keys=True))

if __name__=="__main__":
    main()
