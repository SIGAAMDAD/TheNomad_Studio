#!/usr/bin/env python3
"""Deterministic recipient inbox projections for the Studio communications archive.

Inboxes are replaceable derived views, NOT authoritative job state. Raw normalized
events remain append-only on studio/comms. This module never executes instructions,
grants a lease, certifies QA, spends credits or contacts a model.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import tempfile

try:
    from scripts import studio_comms as comms
except ModuleNotFoundError:
    import studio_comms as comms

MAX_EVENTS = 100000
MAX_INBOX_MESSAGES = 5000


class InboxError(ValueError):
    """Stable failure code, with no untrusted body reflected."""


def archive_events(root):
    """Check canonical paths and critical trust fields before projecting."""
    source = pathlib.Path(root) / "events"
    paths = sorted(source.glob("*/*/*/evt-*.json"))
    if len(paths) > MAX_EVENTS:
        raise InboxError("EVENT_LIMIT_EXCEEDED")
    found = {}
    for path in paths:
        try:
            record = comms.read_json(path)
        except (ValueError, OSError) as exc:
            raise InboxError("INVALID_ARCHIVED_JSON") from exc
        if not isinstance(record, dict):
            raise InboxError("INVALID_ARCHIVED_EVENT")
        key = record.get("event_id")
        if (not isinstance(key, str) or not re.fullmatch(r"evt-[0-9a-f]{32}", key)
            or path.stem != key or key in found
            or record.get("schema_version") != 1
            or record.get("delivery_authority") != "ARCHIVE_ONLY"
            or record.get("identity_assurance") not in
              ("UNVERIFIED_SHARED_OWNER", "REGISTRY_MATCHED_SHARED_CONNECTOR")
            or record.get("event_type") not in comms.EVENTS
            or not isinstance(record.get("recipient"), dict)
            or not isinstance(record["recipient"].get("job_id"), str)
            or not isinstance(record.get("worker_id"), str)
            or not isinstance(record.get("job_id"), str)
            or not isinstance(record.get("summary"), str)
            or type(record.get("requires_ack")) is not bool):
            raise InboxError("INVALID_ARCHIVED_EVENT")
        try:
            stamp = comms.iso_utc(record.get("recorded_at_utc"))
        except comms.MessageError as exc:
            raise InboxError("INVALID_ARCHIVED_TIMESTAMP") from exc
        if stamp[0:10].replace("-", "/") != path.parent.relative_to(source).as_posix():
            raise InboxError("ARCHIVED_EVENT_WRONG_BUCKET")
        found[key] = record
    return sorted(found.values(), key=lambda x: (x["recorded_at_utc"], x["event_id"]))


def routed_workers(event, registry):
    """Routes to current assigned job, optionally a single explicit recipient."""
    job = event["recipient"]["job_id"]
    explicit = event["recipient"].get("worker_id")
    if explicit is not None:
        match = registry.get(explicit)
        if (not match or match.get("status") != "ASSIGNED"
            or match.get("job_id") != job or explicit == event["worker_id"]):
            return []
        return [explicit]
    return sorted(worker for worker, entry in registry.items()
                  if entry.get("status") == "ASSIGNED" and entry.get("job_id") == job
                  and worker != event["worker_id"])


def recipient_is_valid_ack(ack, original, registry):
    """ACK is valid only from an actual designated consumer of this event."""
    if not original.get("requires_ack") or ack["worker_id"] == original["worker_id"]:
        return False
    if ack["worker_id"] not in routed_workers(original, registry):
        return False
    if ack.get("job_id") != original["recipient"]["job_id"]:
        return False
    # Reply must be directed to the original author/job. An ACK is not approval.
    if ack["recipient"]["job_id"] != original["job_id"]:
        return False
    explicit = ack["recipient"].get("worker_id")
    if explicit is not None and explicit != original["worker_id"]:
        return False
    return True


def build(events, registry, jobs):
    if not isinstance(registry, dict) or not isinstance(jobs, dict):
        raise InboxError("REGISTRY_OR_JOBS_MISSING")
    delivered = collections.defaultdict(list)
    waiting = collections.defaultdict(list)
    acknowledgments = collections.defaultdict(dict)
    invalid_ack_ids = []
    event_by_id = {event["event_id"]: event for event in events}

    for event in events:
        event_id = event["event_id"]
        if event["event_type"] == "ACK":
            cause = event.get("causation_id")
            original = event_by_id.get(cause)
            if original and recipient_is_valid_ack(event, original, registry):
                acknowledgments[original["event_id"]].setdefault(
                    event["worker_id"], event_id)
            else:
                invalid_ack_ids.append(event_id)
            continue

        recipients = routed_workers(event, registry)
        if not recipients:
            waiting[event["recipient"]["job_id"]].append({
                "event_id": event_id,
                "reason": "AWAITING_REGISTERED_RECIPIENT",
                "requires_ack": event["requires_ack"],
            })
            continue
        for worker in recipients:
            acknowledged = acknowledgments.get(event_id, {}).get(worker)
            # ACK events may occur later than the original; defer state to second pass.
            delivered[worker].append({
                "event_id": event_id,
                "job_id": event["recipient"]["job_id"],
                "source_job_id": event["job_id"],
                "sender_worker_id": event["worker_id"],
                "event_type": event["event_type"],
                "attempt_id": event.get("attempt_id"),
                "ledger_event_id": event.get("ledger_event_id"),
                "elapsed_seconds": event.get("elapsed_seconds"),
                "resume_required": event.get("resume_required"),
                "timestamp_utc": event["recorded_at_utc"],
                "summary": event["summary"],
                "requires_ack": event["requires_ack"],
                "ack_event_id": acknowledged,
                "source_ref": event.get("source_ref"),
                "source_verification": event.get("source_verification", "NOT_RUN"),
                "delivery_authority": "ARCHIVE_ONLY",
            })

    for worker, messages in delivered.items():
        for message in messages:
            message["ack_event_id"] = acknowledgments.get(
                message["event_id"], {}).get(worker)

    result = {}
    for worker, registration in sorted(registry.items()):
        messages = delivered[worker]
        if len(messages) > MAX_INBOX_MESSAGES:
            raise InboxError("INBOX_BACKLOG_LIMIT_EXCEEDED")
        messages.sort(key=lambda item: (item["timestamp_utc"], item["event_id"]))
        result[worker] = {
            "schema_version": 1, "worker_id": worker,
            "job_id": registration.get("job_id"),
            "assignment_epoch": registration.get("assignment_epoch"),
            "registered_status": registration.get("status"),
            "identity_assurance": "SHARED_OWNER_CONNECTOR_NOT_INDEPENDENTLY_AUTHENTICATED",
            "authoritative": False, "source": "studio/comms immutable events",
            "pending": sum(1 for m in messages if m["requires_ack"] and not m["ack_event_id"]),
            "acknowledged": sum(1 for m in messages if m["ack_event_id"]),
            "messages": messages,
        }
    return result, dict(waiting), sorted(invalid_ack_ids)


def atomic_write_json(path, data):
    """Write a derived projection only if changed; the raw archive is untouched."""
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, sort_keys=True, indent=2) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return False
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                     prefix=".inbox-", delete=False) as writer:
        writer.write(content)
        temporary = pathlib.Path(writer.name)
    temporary.replace(target)
    return True


def materialize(root, registry, jobs):
    events = archive_events(root)
    inboxes, waiting, invalid_acks = build(events, registry, jobs)
    folder = pathlib.Path(root) / "inboxes"
    changed = 0
    expected_current = set()
    expected_unassigned = set()
    for worker, view in inboxes.items():
        if not comms.WORKER.fullmatch(worker):
            raise InboxError("INVALID_INBOX_WORKER_NAME")
        dest = folder / "current" / (worker + ".json")
        expected_current.add(dest.name)
        changed += atomic_write_json(dest, view)
    for job, items in sorted(waiting.items()):
        if not comms.IDENT.fullmatch(job):
            raise InboxError("INVALID_UNASSIGNED_JOB_NAME")
        dest = folder / "unassigned" / (job + ".json")
        expected_unassigned.add(dest.name)
        changed += atomic_write_json(dest, {
            "schema_version": 1, "job_id": job, "authoritative": False,
            "message_count": len(items), "messages": items,
        })
    # No silent stale inbox after reassignment: drop only derived views.
    for category, expected in (("current", expected_current),
                               ("unassigned", expected_unassigned)):
        for stale in (folder / category).glob("*.json"):
            if stale.name not in expected:
                stale.unlink()
                changed += 1
    return {
        "schema_version": 1, "archived_events_examined": len(events),
        "registered_inboxes": len(inboxes), "waiting_jobs": len(waiting),
        "invalid_ack_count": len(invalid_acks),
        "invalid_ack_event_ids": invalid_acks[:25],
        "projection_files_changed": changed,
        "authoritative": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comms-root", required=True)
    parser.add_argument("--worker-registry", required=True)
    parser.add_argument("--runtime-jobs", required=True)
    args = parser.parse_args(argv)
    try:
        registry = comms.chat_registry(comms.read_json(args.worker_registry))
        jobs = comms.approved_jobs(comms.read_json(args.runtime_jobs))
        print(json.dumps(materialize(args.comms_root, registry, jobs), sort_keys=True))
        return 0
    except (InboxError, comms.MessageError, OSError, ValueError) as exc:
        # Do not print untrusted message contents in workflow logs.
        print(json.dumps({"status": "BLOCKED", "code": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
