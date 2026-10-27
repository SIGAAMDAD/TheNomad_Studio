#!/usr/bin/env python3
"""Generate bounded, exact-SHA, review-first engineering evidence from a git diff.

This script never marks compilation or QA PASS by inference. Detailed diffs,
SARIF and ASTs stay as artifacts; the reviewer reads INDEX.md first.
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess


VALID_STATUSES = {"PASS", "FAIL", "NOT_RUN", "PARTIAL", "BLOCKED"}
CPP_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".hxx"}
CS_SUFFIXES = {".cs"}


def git(root, *args, binary=False):
    cmd = ["git", "-C", str(root), *args]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode:
        raise ValueError(f"git {args[0]} failed: {result.stderr.decode(errors='replace')[:600]}")
    return result.stdout if binary else result.stdout.decode("utf-8", "replace").strip()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_json(path, default):
    if not path:
        return default
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def sarif_findings(path):
    if not path:
        return []
    data = load_json(path, {})
    findings = []
    for run in data.get("runs", []):
        for finding in run.get("results", []):
            loc = (finding.get("locations") or [{}])[0].get("physicalLocation") or {}
            artifact = loc.get("artifactLocation") or {}
            path_value = artifact.get("uri", "")
            line = (loc.get("region") or {}).get("startLine")
            message = (finding.get("message") or {}).get("text", "")
            key = [finding.get("ruleId", ""), path_value, message]
            findings.append({
                "fingerprint": sha256(json.dumps(key, separators=(",", ":")).encode())[:16],
                "rule": key[0], "path": path_value, "line": line,
                "level": finding.get("level", "warning"),
                "message": message[:280],
            })
    return findings


def collect_changes(root, base, head):
    raw = git(root, "diff", "--numstat", "--no-renames", base, head, "--")
    files = []
    for line in raw.splitlines():
        values = line.split("\t", 2)
        if len(values) != 3:
            continue
        added, deleted, name = values
        suffix = pathlib.PurePosixPath(name).suffix.lower()
        files.append({
            "path": name, "added": int(added) if added.isdigit() else None,
            "removed": int(deleted) if deleted.isdigit() else None,
            "language": "C++" if suffix in CPP_SUFFIXES else
                        "C#" if suffix in CS_SUFFIXES else "other"
        })
    return sorted(files, key=lambda item: item["path"])


def build_packet(root, base, head, job, out, checks_path=None,
                 sarif_path=None, baseline_sarif_path=None, ast_path=None):
    root, out = pathlib.Path(root).resolve(), pathlib.Path(out).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", base) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("base and head must be complete lowercase 40-character commit SHAs")
    if git(root, "rev-parse", "--verify", base + "^{commit}") != base:
        raise ValueError("Base does not resolve to its asserted SHA")
    if git(root, "rev-parse", "--verify", head + "^{commit}") != head:
        raise ValueError("Head does not resolve to its asserted SHA")
    common = git(root, "merge-base", base, head)
    if common != base:
        raise ValueError("Base is not an ancestor of head; cannot review a bounded patch")
    out.mkdir(parents=True, exist_ok=True)
    patch = git(root, "diff", "--binary", "--unified=3", base, head, "--", binary=True)
    (out / "FULL_DIFF.patch").write_bytes(patch)
    changes = collect_changes(root, base, head)
    all_findings = sarif_findings(sarif_path)
    before = {f["fingerprint"] for f in sarif_findings(baseline_sarif_path)}
    introduced = [f for f in all_findings if f["fingerprint"] not in before]
    checks = load_json(checks_path, {}).get("checks", []) if checks_path else []
    if not isinstance(checks, list):
        raise ValueError("checks must be a list")
    for check in checks:
        if check.get("status") not in VALID_STATUSES:
            raise ValueError("Check missing recognized status")
        if check.get("head_sha") not in (None, head):
            raise ValueError("Stale check evidence for a different commit SHA")
    if not checks:
        checks = [{"name": "automated source verification", "status": "NOT_RUN",
                   "reason": "No exact-SHA checks file was supplied"}]
    symbols = load_json(ast_path, {"status": "NOT_RUN", "translation_units": []})
    if not isinstance(symbols, dict):
        raise ValueError("AST evidence must be a JSON object")
    failed = [c for c in checks if c["status"] in ("FAIL", "BLOCKED")]
    pending = [c for c in checks if c["status"] in ("NOT_RUN", "PARTIAL")]
    failures = {
        "schema": 1, "head_sha": head,
        "failed_checks": failed, "incomplete_checks": pending,
        "new_static_findings": introduced[:160],
        "new_static_findings_total": len(introduced),
        "diagnostics_baseline": "COMPARED" if baseline_sarif_path else "UNAVAILABLE",
    }
    index = {
        "schema": 1, "job_id": job, "base_sha": base, "head_sha": head,
        "diff_sha256": sha256(patch), "diff_bytes": len(patch),
        "changed_file_count": len(changes),
        "inserted_lines": sum(f["added"] or 0 for f in changes),
        "removed_lines": sum(f["removed"] or 0 for f in changes),
        "failed_check_count": len(failed), "incomplete_check_count": len(pending),
        "new_static_findings": len(introduced),
        "ast_status": symbols.get("status", "NOT_RUN"),
        "files": changes[:100],
        "files_omitted": max(0, len(changes) - 100),
    }
    for name, obj in (("INDEX.json", index), ("FINDINGS.json", failures),
                      ("AST_INDEX.json", symbols), ("CHECKS.json", {"checks": checks})):
        (out / name).write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n",
                                encoding="utf-8")
    lines = [
        "# Sol review index — " + job,
        "",
        "Review subject (exact commit): " + head,
        "Baseline: " + base,
        "Diff SHA256: " + index["diff_sha256"],
        "",
        "## Change surface",
        f"{len(changes)} files; +{index['inserted_lines']} / -{index['removed_lines']} lines.",
    ]
    lines += [f"- {c['path']} (+{c['added']}/-{c['removed']})" for c in changes[:30]]
    if len(changes) > 30:
        lines.append(f"- ... plus {len(changes)-30} more (INDEX.json)")
    lines += ["", "## Review first", "1. Read FINDINGS.json for failures and newly introduced diagnostics.",
              "2. Read CHECKS.json and AST_INDEX.json for actual coverage, including NOT_RUN.",
              "3. Fetch targeted changed paths from FULL_DIFF.patch and the exact source commit.",
              "4. Expand into the full AST artifacts or logs only for suspicious symbols.",
              "", "## Evidence status"]
    for check in checks[:30]:
        lines.append(f"- {check.get('name', 'unnamed')}: {check['status']}")
    if len(checks) > 30:
        lines.append(f"- ... {len(checks)-30} further checks in CHECKS.json")
    lines += [
        "", f"New SARIF findings: {len(introduced)}; AST: {index['ast_status']}.",
        "A missing build, tool, static check or AST is NOT_RUN, never PASS.",
        "Do not approve this job solely from this packet; verify actual source, CI and independent QA.",
        ""
    ]
    (out / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    manifest = {}
    for item in sorted(out.iterdir()):
        if item.is_file() and item.name != "MANIFEST.json":
            manifest[item.name] = sha256(item.read_bytes())
    (out / "MANIFEST.json").write_text(json.dumps({
        "schema": 1, "base_sha": base, "head_sha": head,
        "files_sha256": manifest
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return index


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--checks")
    p.add_argument("--sarif")
    p.add_argument("--baseline-sarif")
    p.add_argument("--ast-index")
    args = p.parse_args()
    index = build_packet(args.repo, args.base, args.head, args.job, args.out,
                         args.checks, args.sarif, args.baseline_sarif, args.ast_index)
    print(json.dumps({"job": index["job_id"], "head": index["head_sha"],
                      "changed_files": index["changed_file_count"],
                      "failed_checks": index["failed_check_count"],
                      "incomplete_checks": index["incomplete_check_count"]}))


if __name__ == "__main__":
    main()
