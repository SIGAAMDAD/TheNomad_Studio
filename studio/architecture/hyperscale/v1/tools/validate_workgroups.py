#!/usr/bin/env python3
"""Offline validation of the non-executable hyperscale coordination workgroup plan.

This checks shape, dependencies, wave sequencing and obvious ownership overlap.
It does NOT verify the proposed architecture's implementation or load capacity.
"""
from __future__ import annotations

import fnmatch
import json
import pathlib
import sys

REQUIRED = frozenset((
    "id", "title", "wave", "scope", "objective", "depends_on",
    "integration_gate", "allowed_paths", "protected_paths",
    "consume_contracts", "publish_contracts", "tasks",
    "stop_conditions", "verification", "deliverables"
))


def overlap(a: str, b: str) -> bool:
    if a == b:
        return True
    ap = a.split("*", 1)[0].rstrip("/")
    bp = b.split("*", 1)[0].rstrip("/")
    return (bool(ap and bp and (ap.startswith(bp + "/") or bp.startswith(ap + "/")))
            or fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a))


def validate(doc: dict) -> list[str]:
    errors: list[str] = []
    groups = doc.get("workgroups", [])
    waves = doc.get("waves", [])
    if not isinstance(groups, list) or not isinstance(waves, list):
        return ["workgroups/waves must be lists"]
    by_id = {g.get("id"): g for g in groups if isinstance(g, dict)}
    if len(by_id) != len(groups):
        errors.append("duplicate or invalid workgroup IDs")
    wave_ids = {wave.get("id") for wave in waves if isinstance(wave, dict)}
    for g in groups:
        if not isinstance(g, dict):
            errors.append("invalid group type")
            continue
        group_id = g.get("id")
        missing = REQUIRED.difference(g)
        if missing:
            errors.append(f"{group_id}: missing {sorted(missing)}")
        if g.get("wave") not in wave_ids:
            errors.append(f"{group_id}: unknown wave")
        for dep in g.get("depends_on", []):
            if dep not in by_id:
                errors.append(f"{group_id}: missing dependency {dep}")
            elif dep == group_id:
                errors.append(f"{group_id}: depends on self")
            elif by_id[dep].get("wave", -1) > g.get("wave", -1):
                errors.append(f"{group_id}: future-wave dependency {dep}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(group_id: str) -> None:
        if group_id in visiting:
            errors.append(f"dependency cycle: {group_id}")
            return
        if group_id in visited:
            return
        visiting.add(group_id)
        for dep in by_id[group_id].get("depends_on", []):
            if dep in by_id:
                visit(dep)
        visiting.remove(group_id)
        visited.add(group_id)

    for group_id in by_id:
        visit(group_id)
    for i, left in enumerate(groups):
        for right in groups[i + 1:]:
            if left.get("wave") != right.get("wave"):
                continue
            for first in left.get("allowed_paths", []):
                for second in right.get("allowed_paths", []):
                    if overlap(first, second):
                        errors.append(f"path overlap: {left.get('id')} / {right.get('id')} ({first}, {second})")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_workgroups.py workgroups.json", file=sys.stderr)
        return 2
    doc = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    errors = validate(doc)
    for issue in errors:
        print("ERROR:", issue)
    if errors:
        return 1
    print(f"PASS: {len(doc['workgroups'])} workgroups, {len(doc['waves'])} waves, no dependency or allowed-path conflicts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
