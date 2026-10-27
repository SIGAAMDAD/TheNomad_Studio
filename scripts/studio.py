#!/usr/bin/env python3
"""Deterministic Studio queue and slot controller. Requires Python 3.11+."""
import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
VALID = {"QUEUED", "SCOPED", "READY", "RESERVED", "RUNNING",
         "CHECKPOINTED", "SUBMITTED", "REVIEW", "REWORK",
         "VERIFIED", "ACCEPTED", "BLOCKED", "FAILED", "CANCELLED"}
FINISHED = {"VERIFIED", "ACCEPTED", "CANCELLED"}
ACTIVE = {"RESERVED", "RUNNING", "CHECKPOINTED"}
REVIEW = {"SUBMITTED", "REVIEW"}
def load(name):
    return json.loads((ROOT / "studio" / name).read_text(encoding="utf-8"))

def validate(jobs, slots, config):
    errors = []
    ids = [j.get("id") for j in jobs["jobs"]]
    if len(ids) != len(set(ids)) or any(not x for x in ids):
        errors.append("Job IDs must be unique and nonempty")
    known = set(ids)
    slotids = [s.get("id") for s in slots["slots"]]
    if len(slotids) != len(set(slotids)) or len(slotids) != 10:
        errors.append("Exactly 10 unique slots required")
    if config.get("source_writes_enabled") is not False:
        errors.append("Source writing activation requires reviewed policy change")
    if config.get("auto_ai_enabled") is not False:
        errors.append("Automatic paid AI activation requires reviewed policy change")
    for j in jobs["jobs"]:
        jid = j.get("id", "?")
        if j.get("state") not in VALID:
            errors.append(f"{jid}: invalid state")
        if jid in j.get("dependencies", []):
            errors.append(f"{jid}: self dependency")
        if any(dep not in known for dep in j.get("dependencies", [])):
            errors.append(f"{jid}: missing dependency")
        if j.get("kind") == "code" and j.get("state") in FINISHED | {"SUBMITTED", "REVIEW"}:
            if not (j.get("final_commit_sha") and j.get("drive_report_url")):
                errors.append(f"{jid}: missing final commit or Drive receipt")
        if j.get("state") in {"VERIFIED", "ACCEPTED"} and not j.get("qa_evidence"):
            errors.append(f"{jid}: verified without QA evidence")
    graph = {j["id"]: j.get("dependencies", []) for j in jobs["jobs"] if j.get("id")}
    visiting, visited = set(), set()
    def visit(n):
        if n in visiting:
            errors.append(f"{n}: dependency cycle")
            return
        if n in visited:
            return
        visiting.add(n)
        for dep in graph.get(n, []):
            if dep in graph:
                visit(dep)
        visiting.remove(n)
        visited.add(n)
    for n in graph:
        visit(n)
    assigned = [s.get("job") for s in slots["slots"] if s.get("job")]
    if len(assigned) != len(set(assigned)):
        errors.append("Duplicate worker assignment")
    for s in slots["slots"]:
        if s.get("job") and s["job"] not in known:
            errors.append(f"{s['id']}: unknown job")
    return errors

def dispatch(jobs, slots, config):
    done = {j["id"] for j in jobs["jobs"] if j["state"] in {"VERIFIED", "ACCEPTED"}}
    byid = {j["id"]: j for j in jobs["jobs"]}
    busy = {s["job"] for s in slots["slots"] if s["job"]}
    live_writers = sum(1 for s in slots["slots"] if s["job"] and byid[s["job"]].get("kind") != "review")
    live_reviews = sum(1 for s in slots["slots"] if s["job"] and byid[s["job"]].get("kind") == "review")
    ready = sorted((j for j in jobs["jobs"] if j["state"] == "READY" and
                    j["id"] not in busy and all(d in done for d in j.get("dependencies", []))),
                   key=lambda j: (j.get("priority", 50), j["id"]))
    out = []
    for slot in slots["slots"]:
        if slot["id"] == config["director_slot"] or slot["job"] or not slot["enabled"]:
            continue
        for j in ready:
            is_review = j.get("kind") == "review"
            if is_review and live_reviews >= config["max_reviewers"]:
                continue
            if not is_review and live_writers >= config["max_concurrent_writers"]:
                continue
            if j.get("kind") == "code" and not config["source_writes_enabled"]:
                continue
            # This is advisory dispatch only; does not start ChatGPT or grant a source lease.
            slot["job"] = j["id"]
            slot["epoch"] += 1
            j["state"] = "RESERVED"
            j["slot"] = slot["id"]
            j["assignment_epoch"] = slot["epoch"]
            j["reserved_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            out.append({"slot": slot["id"], "job": j["id"], "epoch": slot["epoch"]})
            busy.add(j["id"])
            ready.remove(j)
            if is_review:
                live_reviews += 1
            else:
                live_writers += 1
            break
    return out

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "dispatch", "status"])
    parser.add_argument("--write", action="store_true", help="Persist dispatch to JSON; never runs AI")
    args = parser.parse_args()
    jobs, slots, config = load("jobs.json"), load("slots.json"), load("config.json")
    errors = validate(jobs, slots, config)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if args.command == "dispatch":
        changes = dispatch(jobs, slots, config)
        print(json.dumps({"reservations": changes}, indent=2))
        if args.write:
            (ROOT / "studio/jobs.json").write_text(json.dumps(jobs, indent=2) + "\n")
            (ROOT / "studio/slots.json").write_text(json.dumps(slots, indent=2) + "\n")
    elif args.command == "status":
        print(json.dumps({"states": {x: sum(j["state"] == x for j in jobs["jobs"]) for x in sorted(VALID)},
                          "busy_slots": [s for s in slots["slots"] if s["job"]]}, indent=2))
    else:
        print("PASS: Studio schema, dependency graph, assignment and evidence gates")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
