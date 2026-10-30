#!/usr/bin/env python3
"""Verify frozen R5 packet integrity and frozen API/worker ownership without changing it."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def check(root: Path) -> dict:
    from studio.app.intake import Roster
    roster = Roster.load(root)
    manifest = json.loads((root / "manifest.json").read_text())
    checked = 0
    for name, meta in manifest["files"].items():
        file = root / name
        data = file.read_bytes()
        if len(data) != meta["bytes"] or hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise ValueError(f"Frozen packet file modified: {name}")
        checked += 1
    catalog = json.loads((root / "CONTRACTS/openapi.json").read_text())
    operations = json.loads((root / "CONTRACTS/operation_semantics.json").read_text())
    from_semantics = {x["operation_id"] for x in operations["operations"]}
    from_openapi = {spec["operationId"] for methods in catalog["paths"].values()
                    for spec in methods.values() if isinstance(spec, dict) and "operationId" in spec}
    if from_semantics != from_openapi:
        raise ValueError("Frozen OpenAPI/operation semantics mismatch")
    return {"plan_sha256": roster.plan_sha256, "verified_files": checked,
            "workgroups": len(roster.paths), "operation_ids": len(from_openapi)}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_r5_contracts.py /path/to/frozen_packet")
    print(json.dumps(check(Path(sys.argv[1])), sort_keys=True, indent=2))
