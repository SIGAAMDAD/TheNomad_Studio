"""WG-H external adapter conformance runner (opt-in; NO live adapters bundled).

The independent integrator supplies a command that accepts one JSON scenario on
stdin and emits one JSON receipt on stdout. This is a TEST BRIDGE PROTOCOL,
not a runtime API. Gate verifies exact revision and invariant evidence; this
cannot authenticate the runner or replace an independent QA signature.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
MATRIX = ROOT / "studio/v16/qa/adapter_cases.v1.json"
EXPECTED = {"INVALID_SCOPE_OR_PATH": {"INVALID_SCOPE", "INVALID_PATH", "PROTECTED_PATH"},
            "INVALID_SCOPE_OR_DISABLED": {"INVALID_SCOPE", "DISABLED", "CUTOVER_NOT_APPROVED"}}
SHA = re.compile(r"^[0-9a-f]{40}$")


def evaluate_receipt(case, reply, source_sha, qa_principal, subject_author):
    if not isinstance(reply, dict) or reply.get("source_sha") != source_sha:
        return "REWORK", "SOURCE_SHA_MISMATCH"
    if reply.get("case_id") != case["id"] or reply.get("adapter_owner") != case["owner"]:
        return "REWORK", "CASE_IDENTITY_MISMATCH"
    if reply.get("synthetic") is not False:
        return "REWORK", "SYNTHETIC_CANNOT_PROVE_ADAPTER"
    if not isinstance(reply.get("observed_effects"), int) or isinstance(reply.get("observed_effects"), bool):
        return "REWORK", "MISSING_EFFECT_COUNT"
    if reply["observed_effects"] < 0 or reply["observed_effects"] > case["max_external_effects"]:
        return "REWORK", "UNAUTHORIZED_OR_DUPLICATE_EFFECT"
    if case["expected"] == "VERIFIED_ONE_EFFECT" and reply["observed_effects"] != 1:
        return "REWORK", "MISSING_VERIFIED_EFFECT"
    expected = EXPECTED.get(case["expected"], {case["expected"]})
    if reply.get("observed") not in expected:
        return "REWORK", "UNEXPECTED_RESULT"
    if not isinstance(reply.get("command"), str) or not reply["command"].strip():
        return "REWORK", "COMMAND_NOT_RECORDED"
    if not isinstance(reply.get("evidence_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", reply["evidence_sha256"]):
        return "REWORK", "MISSING_EVIDENCE_DIGEST"
    if not reply.get("isolated_test_resource"):
        return "REWORK", "ISOLATION_NOT_RECORDED"
    if qa_principal == subject_author:
        return "REWORK", "SELF_REVIEW"
    if reply.get("tested_by") != qa_principal:
        return "REWORK", "REVIEWER_IDENTITY_MISMATCH"
    return "PASS_EVIDENCE_PRESENT", "AWAIT_INDEPENDENT_AUTHENTICATION"


def run_adapter(command, source_sha, qa_principal, subject_author, timeout=30, only=None):
    """Command may use a real isolated test environment; never run it implicitly."""
    cases = json.loads(MATRIX.read_text())["cases"]
    selected = [case for case in cases if not only or case["owner"] in only]
    records = []
    for case in selected:
        if not command:
            records.append({"case_id": case["id"], "status": "NOT_RUN", "reason": "NO_ADAPTER_BRIDGE"})
            continue
        request = {"protocol": "WG-H/BRIDGE/1", "case": case,
                   "expected_source_sha": source_sha, "test_only": True}
        try:
            proc = subprocess.run(command, input=json.dumps(request) + "\n", capture_output=True,
                                  text=True, timeout=timeout, check=False)
            if proc.returncode:
                status, reason = "REWORK", "ADAPTER_NONZERO_EXIT"
            else:
                try:
                    reply = json.loads(proc.stdout)
                except ValueError:
                    status, reason = "REWORK", "MALFORMED_ADAPTER_REPLY"
                else:
                    status, reason = evaluate_receipt(case, reply, source_sha, qa_principal, subject_author)
        except (OSError, subprocess.TimeoutExpired):
            status, reason = "REWORK", "ADAPTER_UNAVAILABLE_OR_TIMEOUT"
        records.append({"case_id": case["id"], "status": status, "reason": reason})
    return {"schema_id": "WG-H/ADAPTER-RESULT/1", "source_sha": source_sha,
            "bridge_executed": bool(command), "independent_qa_verdict": "NOT_ISSUED",
            "records": records,
            "gate": "EVIDENCE_ONLY_AWAIT_SIGNED_INDEPENDENT_QA" if records and all(
                r["status"] == "PASS_EVIDENCE_PRESENT" for r in records) else "BLOCKED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True, help="exact integration source commit (40 hex)")
    parser.add_argument("--qa-principal", required=True, help="independent reviewer identity")
    parser.add_argument("--subject-author", required=True, help="implementing author identity")
    parser.add_argument("--owner", action="append", choices=["C", "D", "F", "G"])
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--bridge", nargs=argparse.REMAINDER, help="explicit argv for isolated adapter bridge")
    args = parser.parse_args(argv)
    if not SHA.fullmatch(args.source_sha):
        parser.error("--source-sha must be an exact 40-character lowercase commit SHA")
    if not args.qa_principal.strip() or not args.subject_author.strip() or args.timeout < 1 or args.timeout > 600:
        parser.error("invalid QA principal or timeout")
    report = run_adapter(args.bridge, args.source_sha, args.qa_principal, args.subject_author,
                         timeout=args.timeout, only=args.owner)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized)
    print(serialized, end="")
    return 0 if report["gate"] == "EVIDENCE_ONLY_AWAIT_SIGNED_INDEPENDENT_QA" else 2


if __name__ == "__main__":
    raise SystemExit(main())
