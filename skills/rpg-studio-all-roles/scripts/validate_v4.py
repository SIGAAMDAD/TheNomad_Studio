#!/usr/bin/env python3
"""Validate the 174-role department mapping and V4 ledger event format offline."""
import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
STATES = {"SCOPING", "READY", "IN_PROGRESS", "BLOCKED", "SUBMITTED", "REWORK", "VERIFIED", "OWNER_ACCEPTED", "FINALIZED"}
EVENT_TYPES = {"ISSUE_REGISTERED", "TASK_STARTED", "CHECKPOINT", "ARTIFACT_SUBMITTED", "QA_PASS", "QA_REWORK", "BLOCKED", "DEPENDENCY_UPDATE", "OWNER_DECISION", "CLOSED", "LEDGER_REPAIR"}
CHECKS = {"PASS", "FAIL", "NOT_RUN", "BLOCKED"}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_roster(root=ROOT):
    manifest = load_json(root / "references/roles_manifest.json")
    registry = load_json(root / "references/departments.v4.json")
    roles = manifest["roles"]
    assert len(roles) == 174 == len({r["id"] for r in roles}), "174 unique roles required"
    assert registry["schema_version"] == 4
    assert len(registry["role_to_department"]) == 174
    assert set(registry["role_to_department"]) == {r["id"] for r in roles}
    assert len(registry["departments"]) == 12
    assert set(registry["departments"]) == set(registry["legacy_department_map"].values())
    assert set(registry["departments"]) == set(load_json(root / "references/ledger_event.schema.json")["properties"]["department_id"]["enum"])
    index = (root / "references/ROLE_INDEX.md").read_text(encoding="utf-8")
    for role in roles:
        rid = role["id"]
        dept_id = registry["role_to_department"][rid]
        assert dept_id in registry["departments"]
        assert dept_id == registry.get("role_home_overrides", {}).get(rid, registry["legacy_department_map"][role["department"]]), rid
        assert (root / f"references/{rid}.md").is_file(), rid
        assert rid in index, rid
    for dept_id, dept in registry["departments"].items():
        head = dept["head_role"]
        assert head in registry["role_to_department"], head
        assert registry["role_to_department"][head] == dept_id, f"head not in own department: {head}"
        assert dept["leadership_scope"]
    return len(roles)


def validate_event(event, filename=None, registry=None):
    if registry is None:
        registry = load_json(ROOT / "references/departments.v4.json")
    mandatory = ["schema_version", "event_id", "recorded_at_utc", "request_id", "department_id", "role_id", "mode", "event_type", "status", "summary", "issue", "todo", "dependencies", "artifacts", "checks", "owner_decisions"]
    for field in mandatory:
        assert field in event, f"missing {field}"
    assert event["schema_version"] == 4
    assert EVENT_PATTERN.fullmatch(event["event_id"])
    if filename:
        assert Path(filename).stem == event["event_id"], "event path/id mismatch"
    date = dt.datetime.fromisoformat(event["recorded_at_utc"].replace("Z", "+00:00"))
    assert date.tzinfo is not None and date.utcoffset() == dt.timedelta(0), "UTC timestamp required"
    assert event["department_id"] in registry["departments"]
    assert registry["role_to_department"].get(event["role_id"]) == event["department_id"], "role ledger must use home department"
    assert event["mode"] in {"interactive", "scheduled", "event_work", "controller"}
    assert event["event_type"] in EVENT_TYPES and event["status"] in STATES
    assert isinstance(event["summary"], str) and event["summary"].strip()
    assert event["issue"] is None or (isinstance(event["issue"], dict) and isinstance(event["issue"].get("number"), int) and event["issue"].get("url", "").startswith("https://"))
    if event["event_type"] == "ISSUE_REGISTERED":
        assert event["issue"] is not None, "Issue registration must identify actual Issue"
    for a in event["artifacts"]:
        assert a["uri"] and a["revision"] and isinstance(a["verified"], bool)
    for c in event["checks"]:
        assert c["status"] in CHECKS
        if c["status"] == "PASS":
            assert c.get("evidence_uri"), "passing check requires evidence URI"
    for t in event["todo"]:
        assert t["state"] in {"OPEN", "DOING", "BLOCKED", "DONE"} and t["id"]
    for d in event["dependencies"]:
        assert d["department_id"] in registry["departments"]
        assert d["state"] in {"WAITING", "READY", "RESOLVED", "BLOCKED"}
    assert isinstance(event["owner_decisions"], list)
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--event", type=Path)
    args = p.parse_args()
    count = validate_roster()
    demo = ROOT / "examples/questline-ledger-event.json"
    validate_event(load_json(demo))
    if args.event:
        validate_event(load_json(args.event), args.event.name)
    print(f"V4 OK: {count} unique roles, 12 departments, schema and ledger example validated")


if __name__ == "__main__":
    main()
