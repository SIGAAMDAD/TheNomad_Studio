#!/usr/bin/env python3
"""Deterministic, non-spending Studio Issue-comment communications broker.

All messages are untrusted data: never execute worker instructions, change
runtime job state, invoke a model, grant source permissions or merge code.
Python 3.11+ standard library only.
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

HUB = "SIGAAMDAD/TheNomad_Studio"
OWNER = "SIGAAMDAD"
FENCE = re.escape(chr(96) * 3)
MARKER = re.compile(r"\A\s*" + FENCE + r"studio-message\s*\n(.*?)\n" + FENCE + r"\s*\Z", re.S)
ISSUE_REF = re.compile(r"https://github\.com/SIGAAMDAD/TheNomad_Studio/issues/([1-9]\d*)(?:[/?#]|$)")
SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,79}\Z")
WORKER = re.compile(r"(?:CW-[A-Z0-9-]{3,70}|S0[0-9])\Z")
EVENTS = frozenset(("STATUS", "CHECKPOINT", "DEPENDENCY_REQUEST",
    "DEPENDENCY_RESOLVED", "HANDOFF_READY", "QA_REQUEST",
    "QA_RESULT", "CI_RESULT", "BLOCKED", "ACK"))
ACTIONS = frozenset(("NONE", "ACKNOWLEDGE", "REVIEW",
    "PROVIDE_DEPENDENCY", "REWORK", "ESCALATE"))
DEPARTMENTS = frozenset(("production", "design", "narrative",
    "engineering", "visual_art", "tech_art", "animation", "audio",
    "ux", "qa", "publishing", "studio_ops"))
WIRE_KEYS = frozenset(("schema_version", "job_id", "worker_id",
    "assignment_epoch", "event_type", "recipient_job_id",
    "recipient_department", "summary", "requested_action", "source_ref",
    "evidence_refs", "causation_id", "requires_ack", "role_id", "recipient_worker_id"))
MAX_BODY_BYTES = 8192
MAX_SCAN_PAGES = 10


class MessageError(ValueError):
    """Stable rejection code; do not echo message bodies."""


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MessageError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=no_duplicate_keys)


def parse_wire(body):
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise MessageError("MESSAGE_TOO_LARGE_OR_MISSING")
    match = MARKER.fullmatch(body)
    if not match:
        raise MessageError("NOT_STUDIO_MESSAGE")
    try:
        data = json.loads(match.group(1), object_pairs_hook=no_duplicate_keys)
    except ValueError as exc:
        raise MessageError("INVALID_JSON") from exc
    if not isinstance(data, dict) or not set(data).issubset(WIRE_KEYS):
        raise MessageError("UNSUPPORTED_MESSAGE_FIELDS")
    required = {"schema_version", "job_id", "worker_id", "event_type",
                "recipient_department", "summary"}
    if not required.issubset(data) or data["schema_version"] != 1:
        raise MessageError("INVALID_MESSAGE_VERSION_OR_FIELDS")
    return data


def issue_for_job(job):
    result = set()
    issue = job.get("issue")
    if isinstance(issue, dict) and issue.get("repo") == HUB and type(issue.get("number")) is int:
        result.add(issue["number"])
    if type(job.get("issue_number")) is int:
        result.add(job["issue_number"])
    feature = job.get("parent_feature", "")
    match = re.fullmatch(r"FEATURE-([1-9]\d*)", feature) if isinstance(feature, str) else None
    if match:
        result.add(int(match.group(1)))
    for ref in job.get("source_refs", []):
        if isinstance(ref, str):
            match = ISSUE_REF.search(ref)
            if match:
                result.add(int(match.group(1)))
    return {value for value in result if value > 0}


def approved_jobs(document):
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), list):
        raise MessageError("RUNTIME_JOBS_UNAVAILABLE")
    jobs = {}
    for item in document["jobs"]:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            if item["id"] in jobs:
                raise MessageError("DUPLICATE_RUNTIME_JOB")
            jobs[item["id"]] = item
    return jobs


def chat_registry(document):
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise MessageError("INVALID_CHAT_REGISTRY")
    workers = document.get("workers")
    if not isinstance(workers, list):
        raise MessageError("INVALID_CHAT_REGISTRY")
    result = {}
    for item in workers:
        if (not isinstance(item, dict)
            or not isinstance(item.get("worker_id"), str)
            or not WORKER.fullmatch(item["worker_id"])
            or item["worker_id"] in result
            or item.get("status") not in ("ASSIGNED", "PAUSED", "RETIRED")
            or type(item.get("assignment_epoch")) is not int
            or item["assignment_epoch"] < 0
            or type(item.get("audit_interval_minutes")) is not int
            or not 15 <= item["audit_interval_minutes"] <= 1440):
            raise MessageError("INVALID_CHAT_WORKER_ENTRY")
        result[item["worker_id"]] = item
    return result


def assert_registered_worker(wire, job, registry):
    """A registry match prevents stale assignments; it is NOT login authentication."""
    if registry is None:
        return
    worker = wire["worker_id"]
    if worker.startswith("S0"):
        if job.get("slot") != worker or job.get("assignment_epoch") != wire.get("assignment_epoch"):
            raise MessageError("SCHEDULED_SLOT_ASSIGNMENT_MISMATCH")
        return
    record = registry.get(worker)
    if record is None or record.get("status") != "ASSIGNED":
        raise MessageError("CHAT_WORKER_UNREGISTERED_OR_INACTIVE")
    if (record.get("job_id") != job["id"]
        or record.get("assignment_epoch") != wire.get("assignment_epoch")
        or record.get("role_id") != job.get("role")):
        raise MessageError("CHAT_WORKER_ASSIGNMENT_MISMATCH")
    if wire.get("role_id") is not None and wire["role_id"] != record.get("role_id"):
        raise MessageError("CHAT_WORKER_ROLE_MISMATCH")


def same_workstream(left, right):
    if left["id"] == right["id"]:
        return True
    if left.get("parent_feature") and left.get("parent_feature") == right.get("parent_feature"):
        return True
    return (left["id"] in right.get("dependencies", []) or
            right["id"] in left.get("dependencies", []) or
            left.get("subject_job") == right["id"] or
            right.get("subject_job") == left["id"])


def iso_utc(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (AttributeError, TypeError, ValueError) as exc:
        raise MessageError("INVALID_EVENT_TIMESTAMP") from exc


def validate_wire(wire, jobs, issue_number, registry=None):
    jid, worker, kind = wire["job_id"], wire["worker_id"], wire["event_type"]
    if not isinstance(jid, str) or not IDENT.fullmatch(jid):
        raise MessageError("INVALID_JOB_ID")
    if not isinstance(worker, str) or not WORKER.fullmatch(worker):
        raise MessageError("INVALID_WORKER_ID")
    if kind not in EVENTS:
        raise MessageError("INVALID_EVENT_TYPE")
    job = jobs.get(jid)
    if not job or job.get("approved") is not True or job.get("state") in ("CANCELLED", "FAILED"):
        raise MessageError("JOB_NOT_APPROVED_OR_ACTIVE")
    subject = jobs.get(job.get("subject_job", ""))
    if issue_number not in issue_for_job(job) and (
            subject is None or issue_number not in issue_for_job(subject)):
        raise MessageError("COMMENT_ISSUE_JOB_MISMATCH")
    assert_registered_worker(wire, job, registry)
    department = wire["recipient_department"]
    if not isinstance(department, str) or department not in DEPARTMENTS:
        raise MessageError("INVALID_RECIPIENT_DEPARTMENT")
    target_id = wire.get("recipient_job_id") or jid
    if not isinstance(target_id, str) or not IDENT.fullmatch(target_id):
        raise MessageError("INVALID_RECIPIENT_JOB")
    target = jobs.get(target_id)
    if not target or not same_workstream(job, target):
        raise MessageError("UNAUTHORIZED_CROSS_JOB_MESSAGE")
    explicit = wire.get("recipient_worker_id")
    if explicit is not None:
        if (not isinstance(explicit, str) or not WORKER.fullmatch(explicit)
            or explicit == worker):
            raise MessageError("INVALID_RECIPIENT_WORKER")
        if registry is not None:
            registered = registry.get(explicit)
            if (registered is None or registered.get("status") != "ASSIGNED"
                or registered.get("job_id") != target_id):
                raise MessageError("INVALID_RECIPIENT_WORKER")
    summary = wire["summary"]
    if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 600:
        raise MessageError("INVALID_SUMMARY")
    if any(char in summary for char in "\x00\r"):
        raise MessageError("INVALID_SUMMARY_CONTROL_CHAR")
    if wire.get("requested_action", "NONE") not in ACTIONS:
        raise MessageError("INVALID_REQUESTED_ACTION")
    epoch = wire.get("assignment_epoch")
    if epoch is not None and (type(epoch) is not int or epoch < 0):
        raise MessageError("INVALID_ASSIGNMENT_EPOCH")
    role = wire.get("role_id")
    if role is not None and (not isinstance(role, str)
                            or not re.fullmatch(r"rpg-[a-z0-9-]+", role)
                            or role != job.get("role")):
        raise MessageError("ROLE_JOB_MISMATCH")
    evidence = wire.get("evidence_refs", [])
    if not isinstance(evidence, list) or len(evidence) > 12 or any(
        not isinstance(ref, str) or len(ref) > 350 or
        not ref.startswith("https://github.com/SIGAAMDAD/") for ref in evidence):
        raise MessageError("INVALID_EVIDENCE_REFS")
    source = wire.get("source_ref")
    if source is not None:
        if (not isinstance(source, dict) or set(source) != {"repo", "ref", "sha"}
            or source["repo"] != job.get("repository")
            or not isinstance(source["ref"], str)
            or not 1 <= len(source["ref"]) <= 140
            or not isinstance(source["sha"], str) or not SHA.fullmatch(source["sha"])):
            raise MessageError("INVALID_SOURCE_REF")
    causation = wire.get("causation_id")
    if causation is not None and (not isinstance(causation, str)
        or not re.fullmatch(r"evt-[0-9a-f]{32}", causation)):
        raise MessageError("INVALID_CAUSATION_ID")
    if kind == "ACK" and (not causation or wire.get("requires_ack", False)):
        raise MessageError("ACK_REQUIRES_CAUSATION_NO_ACK")
    if type(wire.get("requires_ack", False)) is not bool:
        raise MessageError("INVALID_ACK_FLAG")
    return target_id


def normalize_comment(comment, issue_number, jobs, source_repo=HUB, registry=None):
    if source_repo != HUB or type(issue_number) is not int or issue_number <= 0:
        raise MessageError("WRONG_SOURCE_REPOSITORY")
    wire = parse_wire(comment.get("body"))
    actor = (comment.get("user") or {}).get("login")
    if actor != OWNER:
        raise MessageError("UNAUTHORIZED_GITHUB_ACTOR")
    comment_id = comment.get("id")
    if type(comment_id) is not int or comment_id < 1:
        raise MessageError("INVALID_COMMENT_ID")
    target = validate_wire(wire, jobs, issue_number, registry)
    digest = hashlib.sha256(f"{source_repo}:{comment_id}".encode("ascii")).hexdigest()[:32]
    return {
        "schema_version": 1, "event_id": f"evt-{digest}",
        "recorded_at_utc": iso_utc(comment.get("created_at")),
        "event_type": wire["event_type"], "job_id": wire["job_id"],
        "worker_id": wire["worker_id"],
        "assignment_epoch": wire.get("assignment_epoch"),
        "role_id": wire.get("role_id"),
        "identity_assurance": "REGISTRY_MATCHED_SHARED_CONNECTOR" if registry is not None else "UNVERIFIED_SHARED_OWNER",
        "delivery_authority": "ARCHIVE_ONLY",
        "recipient": {"department_id": wire["recipient_department"],
                      "job_id": target, "worker_id": wire.get("recipient_worker_id")},
        "transport": {"repo": source_repo, "issue_number": issue_number,
                      "comment_id": comment_id, "actor_login": actor},
        "summary": wire["summary"].strip(),
        "requested_action": wire.get("requested_action", "NONE"),
        "requires_ack": wire.get("requires_ack", False),
        "causation_id": wire.get("causation_id"),
        "source_ref": wire.get("source_ref"),
        "source_verification": "NOT_RUN",
        "evidence_refs": wire.get("evidence_refs", []),
    }


def write_event(root, event):
    date = dt.datetime.fromisoformat(event["recorded_at_utc"].replace("Z", "+00:00"))
    path = pathlib.Path(root) / "events" / date.strftime("%Y/%m/%d") / (event["event_id"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(event, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise MessageError("EVENT_ID_COLLISION")
        return False
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
    return True


def github_get(token, url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "nomad-studio-comms",
    })
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def scan_comments(jobs, token):
    if not token:
        raise MessageError("GITHUB_TOKEN_MISSING")
    issues = sorted({issue for job in jobs.values()
                     if job.get("approved") is True for issue in issue_for_job(job)})
    for issue in issues:
        for page in range(1, MAX_SCAN_PAGES + 1):
            url = f"https://api.github.com/repos/{HUB}/issues/{issue}/comments?per_page=100&page={page}"
            comments = github_get(token, url)
            if not isinstance(comments, list):
                raise MessageError("INVALID_GITHUB_ISSUE_COMMENTS")
            for comment in comments:
                yield issue, comment
            if len(comments) < 100:
                break
        else:
            raise MessageError(f"COMMENT_SCAN_LIMIT_ISSUE_{issue}")


def digest_day(root, day):
    path = pathlib.Path(root) / "events" / day.replace("-", "/")
    records = [read_json(p) for p in sorted(path.glob("evt-*.json"))]
    counts = collections.Counter(item["event_type"] for item in records)
    latest = {}
    for item in sorted(records, key=lambda entry: (entry["recorded_at_utc"], entry["event_id"])):
        latest[item["worker_id"]] = item
    lines = [f"# Studio communications digest — {day}", "",
             f"Immutable events: {len(records)}", "", "## Event counts", ""]
    lines.extend(f"- {key}: {value}" for key, value in sorted(counts.items()))
    lines.extend(["", "## Latest observed per worker", ""])
    for worker, item in sorted(latest.items()):
        summary = item["summary"].replace("\n", " ").replace("|", "/").replace(chr(96), "'")[:150]
        lines.append(f"- {worker}: {item['event_type']} / {item['job_id']} ({item['event_id']}) — {summary}")
    content = "\n".join(lines) + "\n"
    folder = pathlib.Path(root) / "daily"
    folder.mkdir(parents=True, exist_ok=True)
    for version in range(1, 1000):
        output = folder / f"{day}-v{version}.md"
        if not output.exists():
            output.write_text(content, encoding="utf-8")
            return str(output), len(records)
        if output.read_text(encoding="utf-8") == content:
            return str(output), len(records)
    raise MessageError("DAILY_DIGEST_VERSION_EXHAUSTED")


def audit(root, jobs, registry=None):
    # Include the last eight UTC days; older raw history is retained.
    now_day = dt.datetime.now(dt.timezone.utc).date()
    paths = []
    for offset in range(8):
        bucket = (now_day - dt.timedelta(days=offset)).strftime("%Y/%m/%d")
        paths.extend((pathlib.Path(root) / "events" / bucket).glob("evt-*.json"))
    records = [read_json(p) for p in sorted(paths)]
    latest = {}
    for record in sorted(records, key=lambda e: (e["recorded_at_utc"], e["event_id"])):
        latest[record["worker_id"]] = {
            "job_id": record["job_id"], "event_type": record["event_type"],
            "event_id": record["event_id"], "last_observed_at": record["recorded_at_utc"],
            "verified_execution": False, "identity_assurance": record["identity_assurance"],
        }
    if registry is not None:
        now = dt.datetime.now(dt.timezone.utc)
        for name, entry in registry.items():
            observed = latest.get(name)
            if observed:
                last = dt.datetime.fromisoformat(observed["last_observed_at"].replace("Z", "+00:00"))
                if observed["job_id"] != entry.get("job_id"):
                    health = "EVIDENCE_FROM_PREVIOUS_ASSIGNMENT"
                else:
                    health = ("RECENT_EVIDENCE" if now - last <= dt.timedelta(
                        minutes=entry["audit_interval_minutes"]) else "OVERDUE_EVIDENCE")
            else:
                health = "NOT_OBSERVED"
            latest.setdefault(name, {"event_id": None, "event_type": None,
                                     "last_observed_at": None, "verified_execution": False})
            latest[name].update(registered_status=entry["status"],
                                assigned_job_id=entry.get("job_id"),
                                audit_interval_minutes=entry["audit_interval_minutes"],
                                audit_health=health)
    return {"schema_version": 1, "observed_workers": len(latest),
            "events": len(records), "last_observed": latest,
            "approved_jobs": sum(1 for job in jobs.values() if job.get("approved") is True),
            "note": "GitHub-observed messages; no inactive ChatGPT execution inferred."}


def snapshot(root, jobs, registry=None):
    report = audit(root, jobs, registry)
    moment = dt.datetime.now(dt.timezone.utc)
    timestamp = moment.strftime("%Y%m%dT%H%M%SZ")
    report["generated_at_utc"] = moment.isoformat(timespec="seconds").replace("+00:00", "Z")
    content = json.dumps(report, sort_keys=True, indent=2) + "\n"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    folder = pathlib.Path(root) / "snapshots" / moment.strftime("%Y/%m/%d")
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{timestamp}-{digest}.json"
    if output.exists():
        if output.read_text(encoding="utf-8") != content:
            raise MessageError("SNAPSHOT_IMMUTABILITY_VIOLATION")
    else:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(content)
    return {"snapshot_path": str(output),
            "registered_or_observed_workers": len(report["last_observed"]),
            "source": "GITHUB_OBSERVED_ONLY"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("event", "reconcile", "digest", "audit", "snapshot"))
    parser.add_argument("--runtime-jobs", required=True)
    parser.add_argument("--comms-root", required=True)
    parser.add_argument("--github-event")
    parser.add_argument("--github-token")
    parser.add_argument("--day")
    parser.add_argument("--worker-registry")
    args = parser.parse_args(argv)
    new, duplicate, rejected = 0, 0, collections.Counter()
    try:
        jobs = approved_jobs(read_json(args.runtime_jobs))
        registry = chat_registry(read_json(args.worker_registry)) if args.worker_registry else None
        if args.mode == "event":
            if not args.github_event:
                raise MessageError("GITHUB_EVENT_MISSING")
            event = read_json(args.github_event)
            issue = event.get("issue") or {}
            if event.get("action") != "created" or issue.get("pull_request"):
                print(json.dumps({"status": "IGNORED_NON_ISSUE_EVENT"}))
                return 0
            inputs = [(issue.get("number"), event.get("comment") or {},
                       (event.get("repository") or {}).get("full_name"))]
        elif args.mode == "reconcile":
            inputs = ((issue, comment, HUB) for issue, comment
                      in scan_comments(jobs, args.github_token))
        else:
            inputs = []
        for issue, comment, repo in inputs:
            try:
                item = normalize_comment(comment, issue, jobs, repo, registry)
                if write_event(args.comms_root, item):
                    new += 1
                else:
                    duplicate += 1
            except MessageError as exc:
                if str(exc) == "NOT_STUDIO_MESSAGE":
                    duplicate += 1
                else:
                    rejected[str(exc)] += 1
        if args.mode == "digest":
            day = args.day or (dt.datetime.now(dt.timezone.utc)
                               - dt.timedelta(days=1)).date().isoformat()
            if not re.fullmatch(r"20\d{2}-[01]\d-[0-3]\d", day):
                raise MessageError("INVALID_DIGEST_DAY")
            path, count = digest_day(args.comms_root, day)
            print(json.dumps({"digest": path, "raw_events": count}))
            return 0
        if args.mode == "audit":
            print(json.dumps(audit(args.comms_root, jobs, registry), sort_keys=True))
            return 0
        if args.mode == "snapshot":
            print(json.dumps(snapshot(args.comms_root, jobs, registry), sort_keys=True))
            return 0
        print(json.dumps({"new": new, "duplicates_or_nonmessages": duplicate,
                          "rejected": dict(rejected)}, sort_keys=True))
        return 0
    except (MessageError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
