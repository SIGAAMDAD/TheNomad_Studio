#!/usr/bin/env python3
"""Source-grounded patch DAG, workgroup crosswalk and safety-gate validator.

This validates PLANS, not actual runtime correctness, deployment, or security.
"""
from __future__ import annotations
import copy
import json
import pathlib
import re
import sys

REQUIRED = (
    "id", "title", "owner", "wave", "priority", "depends_on", "source_evidence",
    "objective", "expected_touch_points", "contracts", "preserves", "out_of_scope",
    "verification", "completion_gate"
)


def check(workgroups, program):
    errors = []
    if workgroups.get("project") != "The Nomad Studio Hyperscale Coordinator V2":
        errors.append("BAD_PROJECT")
    if program.get("schema_version") != 1 or program.get("state") != "PROPOSED_NOT_DEPLOYED":
        errors.append("PROGRAM_MUST_BE_PROPOSED_V1")
    patches = program.get("patches", [])
    groups = workgroups.get("workgroups", [])
    if not isinstance(patches, list) or not isinstance(groups, list):
        return ["INVALID_LIST"]
    pmap, gmap = {}, {}
    for g in groups:
        if not isinstance(g, dict) or g.get("id") in gmap:
            errors.append("BAD_OR_DUPLICATE_GROUP")
        else:
            gmap[g["id"]] = g
    if len(groups) != 10 or len(workgroups.get("waves", [])) != 4:
        errors.append("REVIEW_EXISTING_TEN_GROUP_FOUR_WAVE_PROGRAM")
    for p in patches:
        if not isinstance(p, dict) or any(k not in p for k in REQUIRED):
            errors.append("INVALID_PATCH_FIELDS")
            continue
        pid = p["id"]
        if not isinstance(pid, str) or not re.fullmatch(r"H[0-9]{2}", pid) or pid in pmap:
            errors.append("DUPLICATE_OR_INVALID_PATCH_ID")
            continue
        pmap[pid] = p
        owner = gmap.get(p["owner"])
        if owner is None:
            errors.append(f"{pid}:UNKNOWN_OWNER")
            continue
        if owner.get("wave") != p["wave"]:
            errors.append(f"{pid}:PATCH_WAVE_MISMATCH")
        if p.get("priority") not in ("BLOCKING", "HIGH", "MEDIUM"):
            errors.append(f"{pid}:INVALID_PRIORITY")
        for key in ("source_evidence", "contracts", "preserves",
                    "out_of_scope", "verification", "completion_gate"):
            items = p.get(key)
            if not isinstance(items, list) or len(items) < 1 or not all(
                isinstance(x, str) and x.strip() for x in items
            ):
                errors.append(f"{pid}:{key}_EMPTY")
        if not isinstance(p["objective"], str) or not p["objective"].strip():
            errors.append(f"{pid}:OBJECTIVE_EMPTY")
    if sorted(pmap) != [f"H{i:02}" for i in range(15)]:
        errors.append("UNEXPECTED_PATCH_SET")
    for pid, p in pmap.items():
        for dep in p["depends_on"]:
            if dep not in pmap:
                errors.append(f"{pid}:MISSING_DEPENDENCY:{dep}")
            elif pmap[dep]["wave"] > p["wave"]:
                errors.append(f"{pid}:FUTURE_WAVE_DEPENDENCY:{dep}")
    seen, visiting = set(), set()
    def dfs(patch_id):
        if patch_id in visiting:
            errors.append(f"CYCLE:{patch_id}")
            return
        if patch_id in seen:
            return
        visiting.add(patch_id)
        for dep in pmap[patch_id]["depends_on"]:
            if dep in pmap:
                dfs(dep)
        visiting.remove(patch_id)
        seen.add(patch_id)
    for pid in pmap:
        dfs(pid)

    def group_requires(gid, other, visiting_groups=None):
        if gid == other:
            return True
        visiting_groups = set() if visiting_groups is None else visiting_groups
        if gid in visiting_groups or gid not in gmap:
            return False
        visiting_groups.add(gid)
        return any(
            dep == other or group_requires(dep, other, visiting_groups.copy())
            for dep in gmap[gid].get("depends_on", [])
        )

    for pid, p in pmap.items():
        for dep in p["depends_on"]:
            if dep in pmap and pmap[dep]["wave"] == p["wave"]:
                if not group_requires(p["owner"], pmap[dep]["owner"]):
                    errors.append(f"{pid}:MISSING_SAME_WAVE_GROUP_DEPENDENCY:{dep}")
    seen_assignment = set()
    for gid, g in gmap.items():
        mapped = g.get("hardening_patch_ids")
        if not isinstance(mapped, list):
            errors.append(f"{gid}:MISSING_HARDENING_MAPPING")
            continue
        for pid in mapped:
            if pid in seen_assignment:
                errors.append(f"{pid}:DOUBLE_WORKGROUP_OWNERSHIP")
            seen_assignment.add(pid)
            if pid not in pmap or pmap[pid]["owner"] != gid:
                errors.append(f"{gid}:WRONG_PATCH_OWNER:{pid}")
        if gid != "I" and any(not path.startswith((
            "services/coordination/", "tests/coordination/"
        )) for path in g.get("allowed_paths", [])):
            errors.append(f"{gid}:PROTECTED_SOURCE_OWNERSHIP")
    if set(pmap) != seen_assignment:
        errors.append("UNASSIGNED_HARDENING_PATCHES")
    if not re.fullmatch(r"[0-9a-f]{40}", workgroups.get("reviewed_snapshot_main_sha", "")):
        errors.append("REVIEWED_MAIN_SHA_MISSING")
    if not program.get("owner_approval_required") is True:
        errors.append("OWNER_APPROVAL_GATE_MISSING")
    return sorted(set(errors))


def main(argv):
    if len(argv) == 2 and argv[1] == "--self-test":
        root = pathlib.Path(__file__).resolve().parents[1]
        groups = json.loads((root / "workgroups.json").read_text(encoding="utf-8"))
        patches = json.loads((root / "hardening_patches.v1.json").read_text(encoding="utf-8"))
        if check(groups, patches):
            print("BASELINE_INVALID", file=sys.stderr)
            return 1
        mutations = [
            lambda g, p: p["patches"].append(copy.deepcopy(p["patches"][0])),
            lambda g, p: p["patches"][0]["depends_on"].append("H14"),
            lambda g, p: g["workgroups"][0]["hardening_patch_ids"].append("H10"),
            lambda g, p: g["workgroups"][0]["allowed_paths"].append("scripts/studio.py"),
        ]
        for mutate in mutations:
            x, y = copy.deepcopy(groups), copy.deepcopy(patches)
            mutate(x, y)
            if not check(x, y):
                print("BAD_PATCH_ACCEPTED", file=sys.stderr)
                return 1
        print("PASS: rejects duplicate, cycle, owner spoof, protected-path mutation")
        return 0
    if len(argv) != 3:
        print("usage: validate_hardening_plan.py workgroups.json hardening_patches.v1.json", file=sys.stderr)
        return 2
    group = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    plan = json.loads(pathlib.Path(argv[2]).read_text(encoding="utf-8"))
    errors = check(group, plan)
    if errors:
        for error in errors:
            print("ERROR:", error, file=sys.stderr)
        return 1
    print(f"PASS: {len(plan['patches'])} hardening patches, {len(group['workgroups'])} owners, four dependency waves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
