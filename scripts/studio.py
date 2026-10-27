#!/usr/bin/env python3
"""The Nomad Studio deterministic controller (Python 3.11+, standard library only).

It does not invoke models, start scheduled chats, verify Drive content, or merge code.
All mutating commands require --write and operate on an explicitly selected tree.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATES = {"QUEUED", "SCOPED", "READY", "RESERVED", "RUNNING", "CHECKPOINTED",
          "SUBMITTED", "REVIEW", "REWORK", "VERIFIED", "ACCEPTED", "BLOCKED",
          "FAILED", "CANCELLED"}
DONE = {"VERIFIED", "ACCEPTED"}
IN_FLIGHT = {"RESERVED", "RUNNING", "CHECKPOINTED"}
REVIEW = {"SUBMITTED", "REVIEW"}
KINDS = {"design", "writing", "art", "docs", "analysis", "code", "integration", "review"}
WRITERS = KINDS - {"review", "analysis"}
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,79}$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def read(root, filename):
    return json.loads((root / "studio" / filename).read_text(encoding="utf-8"))


def save(root, filename, value):
    path = root / "studio" / filename
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def paths(job):
    return [str(p).replace("\\", "/").strip("/") for p in job.get("owned_paths", [])]


def conflicting(left, right):
    if left.get("repository", "") != right.get("repository", ""):
        return False
    for a in paths(left):
        for b in paths(right):
            if a == "*" or b == "*" or a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                return True
    return False


def validate(jobs, slots, config):
    errors = []
    if jobs.get("schema") != 1 or slots.get("schema") != 1 or config.get("schema") != 1:
        errors.append("All control documents require schema=1")
    entries = jobs.get("jobs", [])
    workers = slots.get("slots", [])
    if not isinstance(entries, list) or not isinstance(workers, list):
        return ["jobs and slots must be arrays"]
    ids = [j.get("id") for j in entries]
    if any(not isinstance(x, str) or not ID_RE.fullmatch(x) for x in ids) or len(set(ids)) != len(ids):
        errors.append("Job IDs must be unique and safe")
    known = set(x for x in ids if isinstance(x, str))
    worker_ids = [w.get("id") for w in workers]
    if set(worker_ids) != {f"S{i:02}" for i in range(10)} or len(workers) != 10:
        errors.append("Exactly S00 through S09 are required")
    if config.get("director_slot") != "S00":
        errors.append("S00 must remain the Director slot")
    for flag in ("source_writes_enabled", "auto_ai_enabled", "auto_dispatch_enabled"):
        if not isinstance(config.get(flag, False), bool):
            errors.append(f"{flag} must be boolean")
    for limit in ("max_concurrent_writers", "max_reviewers"):
        if not isinstance(config.get(limit), int) or not 0 < config[limit] <= 9:
            errors.append(f"{limit} must be an integer in 1..9")
    allowed_repos = {(r.get("repo"), r.get("branch")) for r in config.get("repos", [])}
    by_id = {j["id"]: j for j in entries if isinstance(j.get("id"), str)}
    for j in entries:
        jid = str(j.get("id", "<missing>"))
        state = j.get("state")
        kind = j.get("kind")
        if state not in STATES or kind not in KINDS:
            errors.append(f"{jid}: unknown state or kind")
        deps = j.get("dependencies", [])
        if not isinstance(deps, list) or any(x not in known or x == jid for x in deps):
            errors.append(f"{jid}: missing, self, or malformed dependency")
        if state in {"READY", "RESERVED", "RUNNING", "CHECKPOINTED"}:
            if j.get("approved") is not True or not j.get("role") or not j.get("acceptance"):
                errors.append(f"{jid}: work admission requires approved, role, acceptance")
            if kind in WRITERS and not paths(j):
                errors.append(f"{jid}: writing job requires owned_paths")
            if kind in {"code", "integration"}:
                if (j.get("repository"), j.get("branch")) not in allowed_repos:
                    errors.append(f"{jid}: source repository/branch not whitelisted")
                if not isinstance(j.get("baseline_sha"), str) or not SHA_RE.fullmatch(j["baseline_sha"]):
                    errors.append(f"{jid}: missing pinned 40-digit baseline SHA")
        if state in {"SUBMITTED", "REVIEW", "VERIFIED", "ACCEPTED"} and kind in {"code", "integration"}:
            if not (isinstance(j.get("final_commit_sha"), str) and
                    SHA_RE.fullmatch(j["final_commit_sha"]) and j.get("drive_report_url")):
                errors.append(f"{jid}: code handoff requires final SHA and Drive report URL")
        if state in {"READY", "RESERVED", "RUNNING", "CHECKPOINTED"} and kind == "review":
            subject = j.get("subject_job")
            if not subject or subject not in deps or subject not in known:
                errors.append(f"{jid}: review requires known subject_job dependency")
        if state in {"VERIFIED", "ACCEPTED"}:
            evidence = j.get("qa_evidence")
            if not isinstance(evidence, dict) or evidence.get("verdict") != "PASS" or not evidence.get("subject_ref"):
                errors.append(f"{jid}: verified job requires exact-subject independent QA PASS")
            elif evidence.get("reviewer_slot") in (j.get("slot"), j.get("submitted_by_slot")) and evidence.get("reviewer_slot"):
                errors.append(f"{jid}: author cannot self-review")
            if kind not in {"code", "integration"} and j.get("artifact_ref") and isinstance(evidence, dict) and evidence.get("subject_ref") != j.get("artifact_ref"):
                errors.append(f"{jid}: QA must target submitted artifact revision")
            if kind in {"code", "integration"}:
                if isinstance(evidence, dict) and evidence.get("subject_ref") != j.get("final_commit_sha"):
                    errors.append(f"{jid}: QA must target final commit SHA")
                ci = j.get("ci_evidence")
                if not isinstance(ci, dict) or ci.get("verdict") != "PASS" or ci.get("subject_sha") != j.get("final_commit_sha"):
                    errors.append(f"{jid}: verified code requires passing CI at final commit")
    graph = {j["id"]: j.get("dependencies", []) for j in entries if isinstance(j.get("id"), str)}
    visited = set()
    visiting = set()

    def walk(jid):
        if jid in visiting:
            errors.append(f"{jid}: dependency cycle")
            return
        if jid in visited:
            return
        visiting.add(jid)
        for dep in graph.get(jid, []):
            if dep in graph:
                walk(dep)
        visiting.remove(jid)
        visited.add(jid)

    for jid in graph:
        walk(jid)
    assigned = []
    in_flight = []
    task_ids = [w.get("scheduled_task_id") for w in workers if w.get("scheduled_task_id")]
    if len(task_ids) != len(set(task_ids)):
        errors.append("Two worker slots cannot reference the same scheduled ChatGPT task")
    for worker in workers:
        sid = worker.get("id")
        jobid = worker.get("job")
        if type(worker.get("enabled")) is not bool or type(worker.get("epoch")) is not int or worker["epoch"] < 0:
            errors.append(f"{sid}: invalid enabled or assignment epoch")
        if sid == "S00" and jobid:
            errors.append("S00 is reserved for coordination")
        if jobid:
            if not worker.get("enabled"):
                errors.append(f"{sid}: disabled slot retains live assignment; fence or resolve first")
            assigned.append(jobid)
            job = by_id.get(jobid)
            if not job or job.get("state") not in IN_FLIGHT:
                errors.append(f"{sid}: assignment has no corresponding active job")
            elif job.get("slot") != sid or job.get("assignment_epoch") != worker.get("epoch"):
                errors.append(f"{sid}: assignment epoch/slot mismatch")
            else:
                in_flight.append(job)
    if len(assigned) != len(set(assigned)):
        errors.append("Duplicate job assigned to multiple slots")
    if sum(j.get("kind") in WRITERS for j in in_flight) > config["max_concurrent_writers"]:
        errors.append("Live writer limit exceeded")
    if sum(j.get("kind") == "review" for j in in_flight) > config["max_reviewers"]:
        errors.append("Live reviewer limit exceeded")
    for i, a in enumerate(in_flight):
        for b in in_flight[i + 1:]:
            if conflicting(a, b):
                errors.append(f"Concurrent writable-path conflict: {a['id']} and {b['id']}")
    return errors


def dispatch(jobs, slots, config, now=None):
    """Reserve work only; it does not wake, pay, or change scheduled ChatGPT tasks."""
    if not config.get("auto_dispatch_enabled", False):
        return []
    now = now or dt.datetime.now(dt.timezone.utc).isoformat()
    by_id = {j["id"]: j for j in jobs["jobs"]}
    done = {j["id"] for j in jobs["jobs"] if j.get("state") in DONE}
    reviewable = {j["id"] for j in jobs["jobs"]
                  if j.get("state") in {"SUBMITTED", "REVIEW", "VERIFIED", "ACCEPTED"}}
    occupied = [by_id[w["job"]] for w in slots["slots"] if w.get("job")]
    writers = sum(j.get("kind") in WRITERS for j in occupied)
    reviewers = sum(j.get("kind") == "review" for j in occupied)
    ready = sorted((j for j in jobs["jobs"] if j.get("state") == "READY" and
                    j.get("approved") is True and
                    all(dep in (reviewable if j.get("kind") == "review" else done)
                        for dep in j.get("dependencies", []))),
                   key=lambda j: (int(j.get("priority", 50)), j["id"]))
    planned = []
    for w in slots["slots"]:
        if w["id"] == "S00" or not w.get("enabled") or w.get("job"):
            continue
        capabilities = set(w.get("capabilities", ["*"]))
        for j in list(ready):
            kind = j["kind"]
            if "*" not in capabilities and kind not in capabilities:
                continue
            if kind in {"code", "integration"} and not config.get("source_writes_enabled", False):
                continue
            if kind in WRITERS and writers >= config["max_concurrent_writers"]:
                continue
            if kind == "review" and reviewers >= config["max_reviewers"]:
                continue
            if any(conflicting(j, other) for other in occupied):
                continue
            w["job"] = j["id"]
            w["epoch"] += 1
            j.update({"state": "RESERVED", "slot": w["id"],
                      "assignment_epoch": w["epoch"], "reserved_at": now})
            planned.append({"slot": w["id"], "job": j["id"], "epoch": w["epoch"]})
            occupied.append(j)
            ready.remove(j)
            writers += kind in WRITERS
            reviewers += kind == "review"
            break
    return planned


def context(jobs, slots, slot_id):
    w = next((s for s in slots["slots"] if s["id"] == slot_id), None)
    if w is None:
        raise ValueError("Unknown slot")
    job = next((j for j in jobs["jobs"] if j["id"] == w.get("job")), None)
    if not w.get("enabled") or not job:
        return {"slot": slot_id, "status": "NO_WORK", "epoch": w["epoch"]}
    selected = ("id", "kind", "role", "role_manual_uri", "state", "assignment_epoch",
                "brief_uri", "brief", "source_refs", "repository", "branch", "baseline_sha",
                "owned_paths", "protected_paths", "acceptance", "checkpoint_ref", "qa_findings",
                "artifact_ref", "drive_report_url")
    return {"slot": slot_id, "status": "ASSIGNED", "epoch": w["epoch"],
            "job": {k: job[k] for k in selected if k in job}}


def record(jobs, slots, receipt):
    """Persist objective partial/blocked evidence; final acceptance remains external."""
    required = ("job_id", "slot_id", "assignment_epoch", "attempt_id", "outcome")
    if any(k not in receipt for k in required):
        raise ValueError("Receipt missing required field")
    if not isinstance(receipt["attempt_id"], str) or not ID_RE.fullmatch(receipt["attempt_id"]):
        raise ValueError("Unsafe attempt ID")
    w = next((s for s in slots["slots"] if s["id"] == receipt["slot_id"]), None)
    j = next((x for x in jobs["jobs"] if x["id"] == receipt["job_id"]), None)
    if not w or not j or w.get("job") != j["id"] or w.get("epoch") != receipt["assignment_epoch"]:
        raise ValueError("Stale or unowned receipt: assignment epoch mismatch")
    if j.get("state") not in IN_FLIGHT:
        raise ValueError("Receipt only applies to an in-flight job")
    result = receipt["outcome"]
    if result == "CHECKPOINTED":
        if not receipt.get("checkpoint_ref"):
            raise ValueError("Checkpoint requires persistent artifact/commit reference")
        j["state"] = "CHECKPOINTED"
        j["checkpoint_ref"] = receipt["checkpoint_ref"]
    elif result == "BLOCKED":
        if not receipt.get("reason"):
            raise ValueError("Blocked receipt requires reason")
        j["state"] = "BLOCKED"
        j["blocker"] = receipt["reason"]
        j.pop("slot", None)
        j.pop("assignment_epoch", None)
        w["job"] = None
    elif result == "SUBMITTED":
        if j["kind"] in {"code", "integration"}:
            sha = receipt.get("final_commit_sha")
            drive = receipt.get("drive_report_url")
            if not isinstance(sha, str) or not SHA_RE.fullmatch(sha) or not (
                    isinstance(drive, str) and drive.startswith("https://docs.google.com/")):
                raise ValueError("Code submission requires independent GitHub and Drive readback: final source SHA and exact report missing")
            if not config.get("source_writes_enabled", False):
                raise ValueError("Source-write gate closed")
            j["final_commit_sha"] = sha
            j["drive_report_url"] = drive
            j["state"] = "SUBMITTED"
        else:
            if not receipt.get("artifact_ref"):
                raise ValueError("Non-code submission requires an exact artifact revision")
            j["state"] = "SUBMITTED"
            j["artifact_ref"] = receipt["artifact_ref"]
            if j["kind"] == "review":
                subject = next((x for x in jobs["jobs"] if x.get("id") == j.get("subject_job")), None)
                review = receipt.get("review_result")
                expected = (subject or {}).get("final_commit_sha") if (subject or {}).get("kind") in (
                    "code", "integration") else (subject or {}).get("artifact_ref")
                if not (subject and subject.get("state") in {"SUBMITTED", "REVIEW"} and
                        isinstance(review, dict) and review.get("verdict") in {"PASS", "REWORK"} and
                        review.get("subject_ref") == expected and
                        isinstance(review.get("findings", []), list) and
                        subject.get("submitted_by_slot") != w["id"]):
                    raise ValueError("Independent QA receipt requires exact submitted subject and structured PASS/REWORK")
                j["review_result"] = {
                    "verdict": review["verdict"], "subject_ref": expected,
                    "findings": review.get("findings", []), "reviewer_slot": w["id"]
                }
        j["submitted_by_slot"] = w["id"]
        j.pop("slot", None)
        j.pop("assignment_epoch", None)
        w["job"] = None
    else:
        raise ValueError("Only CHECKPOINTED, BLOCKED, SUBMITTED outcomes can be recorded")
    j["last_attempt_id"] = receipt["attempt_id"]
    return {"job": j["id"], "outcome": result, "slot": w["id"]}


def process_pending(root, jobs, slots, config):
    """Consume immutable worker receipts, leaving rejected evidence unapplied."""
    applied = []
    directory = root / "studio" / "receipts"
    for path in sorted(directory.glob("*/*.json")):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("Symlink receipt paths are forbidden")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        jid, attempt = receipt.get("job_id"), receipt.get("attempt_id")
        if (not isinstance(jid, str) or not ID_RE.fullmatch(jid) or
                not isinstance(attempt, str) or not ID_RE.fullmatch(attempt) or
                path.parent.name != jid or path.name != attempt + ".json"):
            raise ValueError("Receipt path does not match safe job and attempt IDs")
        job = next((x for x in jobs["jobs"] if x["id"] == jid), None)
        if job is None:
            raise ValueError(f"Unknown job receipt {jid}")
        if attempt in job.get("applied_attempts", []):
            continue
        outcome = record(jobs, slots, receipt)
        job.setdefault("applied_attempts", []).append(attempt)
        errors = validate(jobs, slots, config)
        if errors:
            raise ValueError("Receipt invalidates state: " + "; ".join(errors))
        applied.append(outcome)
    return applied


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=ROOT, help="Root of studio state tree")
    parser.add_argument("command", choices=("validate", "status", "dispatch", "context", "record", "process"))
    parser.add_argument("--slot", default=None)
    parser.add_argument("--receipt", type=pathlib.Path)
    parser.add_argument("--write", action="store_true", help="Persist validated state changes")
    args = parser.parse_args(argv)
    jobs, slots, config = (read(args.root, name) for name in ("jobs.json", "slots.json", "config.json"))
    errors = validate(jobs, slots, config)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if args.command == "status":
        print(json.dumps({"states": {x: sum(j["state"] == x for j in jobs["jobs"]) for x in sorted(STATES)},
                          "busy_slots": [s for s in slots["slots"] if s.get("job")],
                          "auto_dispatch_enabled": config.get("auto_dispatch_enabled", False)}, indent=2))
    elif args.command == "dispatch":
        result = dispatch(jobs, slots, config)
        print(json.dumps({"reservations": result}, indent=2))
        if args.write:
            after = validate(jobs, slots, config)
            if after:
                raise ValueError("Dispatch invalid: " + "; ".join(after))
            save(args.root, "jobs.json", jobs)
            save(args.root, "slots.json", slots)
    elif args.command == "process":
        if not args.write:
            parser.error("process requires --write")
        changes = process_pending(args.root, jobs, slots, config)
        if changes:
            save(args.root, "jobs.json", jobs)
            save(args.root, "slots.json", slots)
        print(json.dumps({"applied_receipts": changes}, indent=2))
    elif args.command == "context":
        if not args.slot:
            parser.error("context requires --slot")
        print(json.dumps(context(jobs, slots, args.slot), indent=2))
    elif args.command == "record":
        if not args.receipt or not args.write:
            parser.error("record requires --receipt and --write")
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        if (not isinstance(receipt.get("job_id"), str) or not ID_RE.fullmatch(receipt["job_id"]) or
                not isinstance(receipt.get("attempt_id"), str) or not ID_RE.fullmatch(receipt["attempt_id"])):
            raise ValueError("Unsafe receipt IDs")
        location = args.root / "studio" / "receipts" / receipt["job_id"] / (receipt["attempt_id"] + ".json")
        if location.exists():
            if json.loads(location.read_text(encoding="utf-8")) != receipt:
                raise ValueError("Attempt ID already exists with different evidence")
            print("Idempotent duplicate receipt; no changes")
            return 0
        result = record(jobs, slots, receipt)
        after = validate(jobs, slots, config)
        if after:
            raise ValueError("Receipt invalid: " + "; ".join(after))
        location.parent.mkdir(parents=True, exist_ok=True)
        location.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        save(args.root, "jobs.json", jobs)
        save(args.root, "slots.json", slots)
        print(json.dumps(result))
    else:
        print("PASS: Studio schema, dependencies, epochs, writer ownership and evidence gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
