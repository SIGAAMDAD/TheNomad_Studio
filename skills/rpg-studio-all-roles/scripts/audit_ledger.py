#!/usr/bin/env python3
"""Offline projection/audit of already-fetched Studio issues and V4 ledger events.

No GitHub/mail access: input is a trusted issue snapshot and an event directory.
The output is a deterministic report and a missing-issue backfill PLAN, not a claim
that any GitHub action or email has been executed.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from validate_v4 import ROOT, load_json, validate_event


def audit(issues, events):
    by_issue, by_department, todo, waits = defaultdict(list), defaultdict(list), {}, []
    issues_by_number = {i["number"]: i for i in issues}
    registry = load_json(ROOT / "references/departments.v4.json")
    for e in sorted(events, key=lambda x: (x["recorded_at_utc"], x["event_id"])):
        validate_event(e, registry=registry)
        n = (e.get("issue") or {}).get("number")
        if n is not None:
            by_issue[n].append(e)
        by_department[e["department_id"]].append(e)
        for t in e["todo"]:
            todo[(e["department_id"], e["request_id"], t["id"])] = {**t, "department": e["department_id"], "request_id": e["request_id"]}
        for d in e["dependencies"]:
            if d["state"] in ("WAITING", "BLOCKED"):
                waits.append({"from": e["department_id"], "request": e["request_id"], "to": d["department_id"], "issue_number": d.get("issue_number"), "reason": d["reason"]})
    missing = [i for n,i in sorted(issues_by_number.items()) if n not in by_issue]
    latest = {n: sorted(rows,key=lambda e:(e["recorded_at_utc"],e["event_id"]))[-1] for n,rows in by_issue.items()}
    return {
        "issues_seen": len(issues_by_number),
        "issues_registered": len(set(issues_by_number).intersection(by_issue)),
        "missing_issue_registrations": [{"number": i["number"], "title": i["title"]} for i in missing],
        "event_count": len(events),
        "department_event_count": {k:len(v) for k,v in sorted(by_department.items())},
        "latest_issue_status": {str(n): e["status"] for n,e in sorted(latest.items())},
        "open_todos": sorted((v for v in todo.values() if v["state"] != "DONE"),key=lambda v:(v["department"],v["request_id"],v["id"])),
        "waiting_on": waits,
        "evidence_limitation": "Ledger-reported statuses only; source CI, external artifact and approvals must be independently reconciled"
    }


def render_md(a):
    lines = ["# The Nomad Studio — Offline Ledger Projection", "", f"Issues in snapshot: {a['issues_seen']} / ledger represented: {a['issues_registered']}", f"Events validated: {a['event_count']}", "", "## Missing issue registrations"]
    lines += [f"- #{x['number']}: {x['title']}" for x in a['missing_issue_registrations']] or ["- None observed"]
    lines += ["", "## Outstanding TODOs"]
    lines += [f"- [{x['state']}] {x['department']} {x['request_id']}/{x['id']}: {x['description']}" for x in a['open_todos']] or ["- None in provided events"]
    lines += ["", "## Cross-department waiting"]
    lines += [f"- {x['from']} / {x['request']} -> {x['to']}: {x['reason']}" for x in a['waiting_on']] or ["- No active dependencies in provided events"]
    lines += ["", "**Unverified by this offline projection:** CI, QA, source revisions, owner approvals, email delivery.", ""]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--issues", required=True, type=Path, help="JSON array of real GitHub issue snapshots")
    p.add_argument("--events", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    issues = load_json(args.issues)
    events = [load_json(p) for p in sorted(args.events.rglob("*.json"))]
    result = audit(issues, events)
    args.output.write_text(render_md(result), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(f"Audit: {result['event_count']} events, {len(result['missing_issue_registrations'])} missing Issue registrations")


if __name__ == "__main__":
    main()
