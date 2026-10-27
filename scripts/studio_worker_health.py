#!/usr/bin/env python3
"""Evidence-only V1 auditor for chat-worker progress, ledger receipts and handoffs."""
from __future__ import annotations
import collections
import datetime as dt
import json
import pathlib

ACTIVE = {"STARTED","HEARTBEAT","CHECKPOINT","YIELDED","BLOCKED","SUBMITTED","STOPPED"}
CLOSING = {"YIELDED","SUBMITTED","STOPPED"}
MAX_FILES = 250000
GRACE = dt.timedelta(minutes=10)

class HealthError(ValueError):
    pass

def stamp(value):
    try:
        parsed=dt.datetime.fromisoformat(value.replace("Z","+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError,AttributeError,TypeError) as exc:
        raise HealthError("INVALID_AUDIT_TIMESTAMP") from exc

def read(path):
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError) as exc:
        raise HealthError("INVALID_AUDIT_JSON") from exc
    if not isinstance(data,dict):
        raise HealthError("INVALID_AUDIT_RECORD")
    return data

def files(root, pattern):
    paths=sorted(pathlib.Path(root).glob(pattern))
    if len(paths)>MAX_FILES:
        raise HealthError("AUDIT_FILE_LIMIT_EXCEEDED")
    return paths

def audit(runtime_root, comms_root, registry, jobs, when=None):
    now=stamp((when or dt.datetime.now(dt.timezone.utc)).isoformat())
    raw=[]
    for day_offset in range(3):
        day=(now.date()-dt.timedelta(days=day_offset)).strftime("%Y/%m/%d")
        for path in files(pathlib.Path(comms_root)/"events"/day,"evt-*.json"):
            item=read(path)
            if item.get("event_id")!=path.stem or item.get("delivery_authority")!="ARCHIVE_ONLY":
                raise HealthError("CORRUPT_COMMS_EVENT")
            if stamp(item.get("recorded_at_utc"))>now+dt.timedelta(minutes=5):
                raise HealthError("FUTURE_COMMS_EVENT")
            raw.append(item)
    ledgers=[]
    for path in files(pathlib.Path(runtime_root)/"studio/ledgers/events","*/*/*.json"):
        item=read(path)
        if item.get("schema_version")!=4 or item.get("event_id")!=path.stem:
            raise HealthError("CORRUPT_LEDGER_EVENT")
        ledgers.append(item)
    results=[]
    counts=collections.Counter()
    for wid,worker in sorted(registry.items()):
        jobid=worker.get("job_id")
        if worker.get("status")!="ASSIGNED":
            results.append({"worker_id":wid,"job_id":jobid,"state":"UNASSIGNED","alerts":[]})
            continue
        interval=worker["audit_interval_minutes"]
        epoch=worker["assignment_epoch"]
        mine=sorted((e for e in raw if e.get("worker_id")==wid and
                     e.get("job_id")==jobid and e.get("assignment_epoch")==epoch and
                     e.get("event_type") in ACTIVE),
                    key=lambda e:(e["recorded_at_utc"],e["event_id"]))
        ledger=[e for e in ledgers if e.get("worker_slot")==wid and e.get("job_id")==jobid]
        alerts=set()
        if not mine and not ledger:
            alerts.add("WORKER_NOT_OBSERVED")
        elif not any(e["event_type"]=="STARTED" for e in mine):
            alerts.add("START_EVENT_MISSING")
        if any(e.get("worker_id")==wid and e.get("job_id")==jobid and
               e.get("event_type") in ACTIVE and e.get("assignment_epoch")!=epoch for e in raw):
            alerts.add("ASSIGNMENT_EPOCH_MISMATCH")
        # A checkpoint or blocking response is substantive progress and must
        # carry both a durable ledger reference and its measured execution time.
        # Allow a short grace period for GitHub's independently scheduled broker.
        for event in mine:
            if event["event_type"] not in ("CHECKPOINT", "BLOCKED"):
                continue
            if now-stamp(event["recorded_at_utc"]) <= GRACE:
                continue
            ledger_ref=event.get("ledger_event_id")
            attempt=event.get("attempt_id")
            if not isinstance(ledger_ref,str) or not ledger_ref:
                alerts.add("PROGRESS_LEDGER_REFERENCE_MISSING")
            elif not any(item.get("event_id")==ledger_ref and
                         item.get("attempt_id")==attempt for item in ledger):
                alerts.add("PROGRESS_LEDGER_MISSING")
            if type(event.get("elapsed_seconds")) is not int:
                alerts.add("PROGRESS_DURATION_MISSING")
        latest=mine[-1] if mine else None
        if latest:
            age=now-stamp(latest["recorded_at_utc"])
            kind=latest["event_type"]
            if age>dt.timedelta(minutes=interval) and kind not in ("SUBMITTED","STOPPED"):
                alerts.add("YIELDED_CONTINUATION_OVERDUE" if kind=="YIELDED" else "HEARTBEAT_OVERDUE")
            if kind in CLOSING and age>GRACE:
                attempt=latest.get("attempt_id")
                matches=[entry for entry in ledger if attempt and entry.get("attempt_id")==attempt]
                if not matches:
                    alerts.add("LEDGER_EVENT_MISSING")
                    if kind=="YIELDED":
                        alerts.add("YIELD_WITHOUT_CHECKPOINT")
                elif latest.get("ledger_event_id") and not any(
                    x.get("event_id")==latest["ledger_event_id"] for x in matches):
                    alerts.add("LEDGER_EVENT_ID_MISMATCH")
                if kind=="SUBMITTED" and any(
                    task.get("state")!="DONE" for item in matches
                    for task in item.get("todo",[]) if isinstance(task,dict)):
                    alerts.add("TERMINAL_WITH_OPEN_TODOS")
        inbox=pathlib.Path(comms_root)/"inboxes/current"/(wid+".json")
        if inbox.exists():
            cache=read(inbox)
            if cache.get("worker_id")!=wid or cache.get("authoritative") is not False:
                raise HealthError("INBOX_PROJECTION_MISMATCH")
            if any(msg.get("requires_ack") is True and not msg.get("ack_event_id") and
                   now-stamp(msg.get("timestamp_utc"))>dt.timedelta(minutes=interval)
                   for msg in cache.get("messages",[])):
                alerts.add("INBOX_ACK_OVERDUE")
        counts.update(alerts)
        results.append({
            "worker_id":wid,"job_id":jobid,
            "state":"EVIDENCE_ALERT" if alerts else ("OBSERVED" if mine else "NOT_OBSERVED"),
            "alerts":sorted(alerts),"last_event_id":latest.get("event_id") if latest else None,
            "last_observed_at":latest.get("recorded_at_utc") if latest else None,
            "ledger_entries":len(ledger),"chat_execution_verified":False})
    return {"schema_version":1,"registered":len(registry),"alerts":dict(sorted(counts.items())),
            "workers":results,"source":"GitHub evidence only; does not observe dormant chats."}
