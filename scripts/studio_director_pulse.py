#!/usr/bin/env python3
"""Read-only, evidence-linked hourly and 23:00 Central Studio workforce reports."""
import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import sys
from zoneinfo import ZoneInfo
try:
    from scripts import studio_comms as comms
    from scripts import studio_worker_health as health
except ModuleNotFoundError:
    import studio_comms as comms
    import studio_worker_health as health

CENTRAL = ZoneInfo("America/Chicago")


class PulseError(ValueError):
    pass


def ci_for_jobs(root):
    result = {}
    folder = pathlib.Path(root) / "evidence" / "source_ci"
    paths = sorted(folder.glob("*/ci-*.json"))
    if len(paths) > 10000:
        raise PulseError("CI_RECEIPT_LIMIT_EXCEEDED")
    for file in paths:
        record = comms.read_json(file)
        if (not isinstance(record, dict)
            or record.get("receipt_id") != file.stem
            or record.get("source_run_metadata_verified") is not True
            or record.get("runtime_promotion_authorized") is not False):
            raise PulseError("UNVERIFIED_CI_RECEIPT")
        job = record.get("job_id")
        if not isinstance(job, str) or not comms.IDENT.fullmatch(job):
            raise PulseError("INVALID_CI_JOB")
        rank = (record.get("run_completed_at") or "", record.get("source_run_id") or 0)
        if job not in result or rank > result[job][0]:
            result[job] = (rank, {
                "source_sha": record.get("source_sha"),
                "source_repo": record.get("source_repo"),
                "run_conclusion": record.get("run_conclusion"),
                "source_run_url": record.get("source_run_url"),
                "artifact_bytes_verified": record.get("artifacts_content_verified") is True,
                "independent_qa": "NOT_ESTABLISHED",
            })
    return {key: value[1] for key, value in result.items()}


def inbox_facts(root, worker):
    if not comms.WORKER.fullmatch(worker):
        raise PulseError("BAD_WORKER_ID")
    path = pathlib.Path(root) / "inboxes" / "current" / (worker + ".json")
    if not path.is_file():
        return {"inbox_status": "NOT_AVAILABLE", "pending_ack": None}
    data = comms.read_json(path)
    if (data.get("worker_id") != worker or data.get("authoritative") is not False
        or type(data.get("pending")) is not int):
        raise PulseError("INBOX_INTEGRITY_FAILED")
    return {"inbox_status": "DERIVED", "pending_ack": data["pending"],
            "acknowledged": data.get("acknowledged"),
            "message_count": len(data.get("messages", []))}


def build(runtime, comms_root, when=None, kind="hourly"):
    if kind not in ("hourly", "daily"):
        raise PulseError("BAD_REPORT_KIND")
    instant = when or dt.datetime.now(dt.timezone.utc)
    if instant.tzinfo is None:
        raise PulseError("NAIVE_TIMESTAMP")
    instant = instant.astimezone(dt.timezone.utc)
    local = instant.astimezone(CENTRAL)
    folder = pathlib.Path(runtime) / "studio"
    jobs = comms.approved_jobs(comms.read_json(folder / "jobs.json"))
    slots = comms.read_json(folder / "slots.json")["slots"]
    registry = comms.chat_registry(comms.read_json(folder / "chat_workers.json"))
    observed = comms.audit(comms_root, jobs, registry)
    ci = ci_for_jobs(comms_root)
    telemetry = health.audit(runtime, comms_root, registry, jobs, when=instant)
    workers = []
    for ident, item in sorted(registry.items()):
        activity = observed["last_observed"].get(ident, {})
        current_job = jobs.get(item.get("job_id"))
        workers.append({
            "worker_id": ident, "job_id": item.get("job_id"),
            "role_id": item.get("role_id"), "status": item["status"],
            "job_state": current_job.get("state") if current_job else None,
            "assignment_epoch": item["assignment_epoch"],
            "audit_health": activity.get("audit_health", "NOT_OBSERVED"),
            "last_event_id": activity.get("event_id"),
            "last_observed_at": activity.get("last_observed_at"),
            "chat_execution_verified": False,
            **inbox_facts(comms_root, ident),
            "source_ci": ci.get(item.get("job_id")),
        })
    missing = []
    if not workers:
        missing.append("NO_CHAT_WORKERS_ENROLLED")
    if not observed["events"]:
        missing.append("NO_RECENT_MESSAGE_EVIDENCE")
    if not ci:
        missing.append("NO_VALIDATED_SOURCE_CI")
    if any(w["inbox_status"] == "NOT_AVAILABLE" for w in workers):
        missing.append("INBOXES_MISSING")
    if telemetry["alerts"]:
        missing.append("WORKER_TELEMETRY_ALERTS")
    counts = dict(sorted(collections.Counter(
        job.get("state", "UNKNOWN") for job in jobs.values()).items()))
    return {
        "schema_version": 1, "report_kind": kind,
        "generated_at_utc": instant.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "date_central": local.date().isoformat(),
        "time_central": local.isoformat(timespec="seconds"),
        "timezone": "America/Chicago",
        "chat_workers": workers, "registered_chat_workers": len(workers),
        "scheduled_slots": [{"id": s.get("id"), "enabled": s.get("enabled"),
                            "job": s.get("job"), "last_execution": "NOT_VERIFIED"}
                           for s in slots],
        "runtime_job_state_counts": counts,
        "source_ci_jobs_with_evidence": len(ci),
        "recent_communications_events": observed["events"],
        "worker_health": telemetry,
        "worker_alerts": telemetry["alerts"],
        "pending_ack_total": sum(w["pending_ack"] or 0 for w in workers),
        "data_gaps": missing, "oversight_case_details_included": False,
        "requires_exact_sha_qa_before_completion": True,
        "note": "Only GitHub-observed activity; cannot wake inactive chats or self-certify QA.",
    }


def write_report(root, record):
    time = dt.datetime.fromisoformat(record["generated_at_utc"].replace("Z", "+00:00"))
    name = time.strftime("%Y%m%dT%H%M%SZ")
    base = pathlib.Path(root) / "reports" / record["report_kind"]
    base /= (record["date_central"] if record["report_kind"] == "daily"
             else time.strftime("%Y/%m/%d"))
    base.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(record, sort_keys=True, indent=2) + "\n"
    dest = base / f"{name}-{hashlib.sha256(raw.encode()).hexdigest()[:16]}.json"
    if dest.exists():
        if dest.read_text(encoding="utf-8") != raw:
            raise PulseError("REPORT_IMMUTABILITY_FAILED")
        return False, str(dest)
    with dest.open("x", encoding="utf-8") as handle:
        handle.write(raw)
    return True, str(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--comms-root", required=True)
    parser.add_argument("--kind", choices=("hourly", "daily"), default="hourly")
    args = parser.parse_args()
    try:
        report = build(args.runtime_root, args.comms_root, kind=args.kind)
        created, path = write_report(args.comms_root, report)
        print(json.dumps({"created": created, "report": path,
                          "registered_workers": report["registered_chat_workers"],
                          "pending_ack": report["pending_ack_total"],
                          "data_gaps": report["data_gaps"]}, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "code": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
