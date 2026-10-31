#!/usr/bin/env python3
"""Intake/stage local submissions. Promotion requires actual independent QA + G readback."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from studio.app.integration import CanonicalTree
from studio.app.intake import Roster


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--integration-root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--intent", required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--author", required=True)
    args = parser.parse_args()
    roster = Roster.load(args.packet)
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    handoff = roster.verify_handoff(args.handoff, metadata)
    local = CanonicalTree(args.integration_root, args.project)
    candidate = local.stage(intent_id=args.intent, project_id=args.project,
                            submissions=[(args.submission, args.author, handoff)])
    print(json.dumps({"status": "STAGED_ONLY_NOT_PROMOTED",
                      "intent_id": candidate.intent_id,
                      "expected_revision_id": candidate.expected_revision_id,
                      "candidate_revision_id": candidate.revision_id,
                      "source_files": len(candidate.tree)}, indent=2))


if __name__ == "__main__":
    main()
