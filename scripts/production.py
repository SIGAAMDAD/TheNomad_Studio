#!/usr/bin/env python3
"""Feature admission, independent QA adjudication, and owner-review tracking.

This module does NOT invoke an AI model, approve canon, merge code, or fabricate
external artifact proof. Its stable data lives exclusively on studio/runtime.
"""
import argparse
import copy
import json
import pathlib
import re
import urllib.parse

import studio
from github_api import GitHub, GitHubError

ROOT = pathlib.Path(__file__).resolve().parents[1]
GIT_REF = re.compile(r"^github:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-fA-F]{40}):(.+)$")
GIT_URL = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(?:blob|commit)/([0-9a-fA-F]{40})(?:/(.+))?$"
)
SOURCE = {"code", "integration"}


def load(root, name):
    return json.loads((root / "studio" / name).read_text(encoding="utf-8"))


def save(root, name, data):
    studio.save(root, name, data)


def _clean_path(path):
    path = str(path).replace("\\", "/").strip("/")
    if not path or any(x in ("", ".", "..") for x in path.split("/")):
        raise ValueError("Invalid owned path")
    return path


def _list(value, name):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{name} must be a list of nonempty strings")
    return value


def _require_spec(feature, spec, existing_jobs):
    fid = feature["id"]
    if spec.get("schema") != 1 or spec.get("feature_id") != fid:
        raise ValueError(f"{fid}: specification schema/feature mismatch")
    if spec.get("source_issue") != feature.get("issue_url"):
        raise ValueError(f"{fid}: source_issue does not match verified issue URL")
    entries = spec.get("jobs")
    if not isinstance(entries, list) or not entries or len(entries) > 60:
        raise ValueError(f"{fid}: 1..60 bounded child jobs required")
    ids = [x.get("id") for x in entries]
    if len(ids) != len(set(ids)) or any(not isinstance(x, str) or
                                       not studio.ID_RE.fullmatch(x) or
                                       not x.startswith(fid + "-") for x in ids):
        raise ValueError(f"{fid}: duplicate, invalid, or unrelated job IDs")
    old = {x["id"] for x in existing_jobs}
    generated = set(ids) | {"QA-" + x for x in ids}
    if generated & old:
        raise ValueError(f"{fid}: child job ID already exists; do not overwrite")
    for item in entries:
        jid = item["id"]
        if item.get("kind") not in studio.KINDS - {"review"}:
            raise ValueError(f"{jid}: invalid producer kind")
        if not item.get("role") or not item.get("role_manual_uri"):
            raise ValueError(f"{jid}: missing specialist role/manual")
        if not item.get("brief") or not item.get("acceptance"):
            raise ValueError(f"{jid}: missing scoped brief or acceptance")
        _list(item.get("acceptance"), f"{jid}.acceptance")
        owned = _list(item.get("owned_paths"), f"{jid}.owned_paths")
        if item["kind"] in studio.WRITERS and not owned:
            raise ValueError(f"{jid}: missing exclusive owned paths")
        for path in owned:
            _clean_path(path)
        dependencies = _list(item.get("dependencies", []), f"{jid}.dependencies")
        if any(dep not in ids and dep not in old for dep in dependencies):
            raise ValueError(f"{jid}: unknown declared dependency")
        if jid in dependencies:
            raise ValueError(f"{jid}: self dependency")
        for dep in dependencies:
            if dep in ids and not next(x for x in entries if x["id"] == dep).get("qa", True):
                raise ValueError(f"{jid}: upstream deliverable must have independent QA")
        if item["kind"] in SOURCE:
            if not item.get("repository") or not item.get("branch") or not item.get("baseline_sha"):
                raise ValueError(f"{jid}: missing actual code source pin (no fabricated SHA)")
            if not studio.SHA_RE.fullmatch(str(item["baseline_sha"])):
                raise ValueError(f"{jid}: invalid source baseline SHA")
        if item.get("qa", True) is not True:
            raise ValueError(f"{jid}: production jobs require independent QA")
        if not item.get("qa_role"):
            raise ValueError(f"{jid}: explicit QA role required")
    if any(len(x) > 80 or not studio.ID_RE.fullmatch(x) for x in generated):
        raise ValueError(f"{fid}: generated QA ID too long")


def build_jobs(feature, spec, config, existing_jobs):
    _require_spec(feature, spec, existing_jobs)
    fid = feature["id"]
    spec_jobs = spec["jobs"]
    own_ids = {x["id"] for x in spec_jobs}
    output = []
    for item in spec_jobs:
        jid, kind = item["id"], item["kind"]
        dependencies = [("QA-" + dep) if dep in own_ids else dep
                        for dep in item.get("dependencies", [])]
        new = {
            "id": jid, "parent_feature": fid, "kind": kind,
            "state": "BLOCKED" if kind in SOURCE else "READY",
            "approved": True, "role": item["role"],
            "role_manual_uri": item["role_manual_uri"], "priority": int(item.get("priority", 50)),
            "brief": item["brief"], "source_refs": item.get("source_refs", []),
            "owned_paths": [_clean_path(p) for p in item.get("owned_paths", [])],
            "protected_paths": item.get("protected_paths", []),
            "repository": item.get("repository", feature.get("repo", "docs")),
            "branch": item.get("branch"), "baseline_sha": item.get("baseline_sha"),
            "acceptance": item["acceptance"], "dependencies": dependencies,
            "qa_owner": "S09" if kind in SOURCE else "S08"
        }
        if kind in SOURCE:
            new["blocker"] = "Engineering source freeze and build/ABI gates require owner approval"
        output.append(new)
        qa_role = item["qa_role"]
        review_id = "QA-" + jid
        output.append({
            "id": review_id, "parent_feature": fid, "kind": "review",
            "state": "READY", "approved": True, "priority": int(item.get("priority", 50)) + 1,
            "role": qa_role, "role_manual_uri": item.get("qa_manual_uri", "skills://" + qa_role),
            "subject_job": jid, "dependencies": [jid],
            "repository": feature.get("repo", "SIGAAMDAD/TheNomad_Studio"),
            "owned_paths": [f"studio/reviews/{review_id}.md"],
            "protected_paths": item.get("protected_paths", []),
            "brief": f"Independently review exact submitted artifact of {jid}. "
                     "Use the subject's accepted baseline, negative cases, and cross-discipline references. "
                     "Publish an immutable review report plus structured verdict and findings.",
            "acceptance": ["Review exact submitted revision", "Record objective PASS/REWORK",
                           "Publish immutable QA report and evidence receipt"]
        })
    candidate = copy.deepcopy(existing_jobs)
    candidate["jobs"].extend(output)
    return output, candidate


def admit(root, features, jobs, slots, config):
    added, errors = [], []
    for feature in features["features"]:
        if not feature.get("approved") or feature.get("jobs") or feature.get("source") != "github_issue":
            continue
        spec_file = root / "studio" / "specs" / (feature["id"] + ".json")
        if not spec_file.is_file():
            feature["state"] = "SCOPING"
            feature["intake_note"] = "Director must create scoped, source-grounded feature spec"
            continue
        try:
            spec = json.loads(spec_file.read_text(encoding="utf-8"))
            children, candidate = build_jobs(feature, spec, config, jobs)
            check = studio.validate(candidate, slots, config)
            if check:
                raise ValueError("; ".join(check))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            feature["state"] = "BLOCKED"
            feature["blocker"] = "Invalid feature spec: " + str(exc)
            errors.append({"feature": feature["id"], "error": str(exc)})
            continue
        jobs = candidate
        feature["jobs"] = [x["id"] for x in children]
        feature["state"] = "PRODUCTION"
        feature["spec_ref"] = f"studio/specs/{feature['id']}.json"
        feature.pop("intake_note", None)
        feature.pop("blocker", None)
        added.extend(x["id"] for x in children)
    return jobs, {"admitted": added, "errors": errors}


def parse_ref(ref):
    if not isinstance(ref, str):
        return None
    match = GIT_REF.fullmatch(ref)
    if match:
        return match.group(1), match.group(2), match.group(3)
    match = GIT_URL.fullmatch(ref)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None


def github_ref_verified(ref, gh):
    parsed = parse_ref(ref)
    if not parsed:
        return False
    repo, sha, path = parsed
    try:
        if path:
            escaped = urllib.parse.quote(path, safe="/")
            result = gh.request("GET", f"/repos/{repo}/contents/{escaped}?ref={sha}")
            return bool(result.get("sha") and result.get("type") == "file")
        commit = gh.request("GET", f"/repos/{repo}/commits/{sha}")
        return commit.get("sha", "").lower() == sha.lower()
    except GitHubError:
        return False


def adjudicate(jobs, gh=None, external_verifier=None):
    """Consume structured reviewer verdicts. Never trust a bare PASS without real readback."""
    changes = []
    byid = {job["id"]: job for job in jobs["jobs"]}
    verify = external_verifier or (lambda ref: bool(gh and github_ref_verified(ref, gh)))
    for reviewer in jobs["jobs"]:
        if reviewer.get("kind") != "review" or reviewer.get("state") != "SUBMITTED":
            continue
        result = reviewer.get("review_result")
        if not isinstance(result, dict):
            continue  # old pilot QA lacks structured review until amended
        subject = byid.get(reviewer.get("subject_job"))
        if not subject:
            continue
        expected = subject.get("final_commit_sha") if subject.get("kind") in SOURCE else subject.get("artifact_ref")
        if not expected or result.get("subject_ref") != expected:
            continue  # never adjudicate stale review of previous artifact
        if subject.get("submitted_by_slot") == result.get("reviewer_slot"):
            continue
        if reviewer.get("last_adjudicated_ref") == expected:
            continue
        readback = reviewer.get("readback_evidence", {})
        external_ok = (isinstance(readback, dict) and readback.get("verified") is True
                       and readback.get("subject_ref") == expected
                       and readback.get("report_ref") == reviewer.get("artifact_ref")
                       and readback.get("verified_by") == "S00"
                       and bool(readback.get("evidence_uri")))
        if not external_ok and (not verify(reviewer.get("artifact_ref")) or not verify(expected)):
            subject["state"] = "REVIEW"
            subject["review_blocker"] = "Independent report or exact subject not externally readable"
            continue
        verdict = result.get("verdict")
        if verdict == "REWORK":
            reviewer["last_adjudicated_ref"] = expected
            subject["qa_findings"] = result.get("findings", [])
            subject["previous_artifact_ref"] = expected
            subject.pop("artifact_ref", None)
            subject.pop("final_commit_sha", None)
            subject.pop("qa_evidence", None)
            subject["state"] = "READY" if subject["kind"] not in SOURCE else "BLOCKED"
            reviewer["state"] = "QUEUED"
            reviewer.pop("review_result", None)
            reviewer.pop("artifact_ref", None)
            changes.append({"job": subject["id"], "outcome": "REWORK"})
        elif verdict == "PASS":
            subject.pop("qa_findings", None)
            subject["qa_evidence"] = {
                "verdict": "PASS", "subject_ref": expected,
                "reviewer_slot": result.get("reviewer_slot"),
                "report_ref": reviewer.get("artifact_ref")
            }
            subject.pop("review_blocker", None)
            if subject["kind"] in SOURCE:
                ci = subject.get("ci_evidence", {})
                complete = (bool(subject.get("drive_report_url")) and
                            ci.get("verdict") == "PASS" and
                            ci.get("subject_sha") == expected)
            else:
                complete = True
            if complete:
                reviewer["last_adjudicated_ref"] = expected
                subject["state"] = "VERIFIED"
                reviewer["state"] = "VERIFIED"
                reviewer["qa_evidence"] = {"verdict": "PASS", "subject_ref": reviewer["artifact_ref"],
                                           "reviewer_slot": "S00"}
                changes.append({"job": subject["id"], "outcome": "VERIFIED"})
            else:
                subject["state"] = "REVIEW"
                changes.append({"job": subject["id"], "outcome": "QA_PASS_CI_OR_HANDOFF_PENDING"})
    # Unlock a queued review only after a revised subject is submitted.
    for reviewer in jobs["jobs"]:
        if reviewer.get("kind") == "review" and reviewer.get("state") == "QUEUED":
            subject = byid.get(reviewer.get("subject_job"))
            if subject and subject.get("state") == "SUBMITTED":
                reviewer["state"] = "READY"
    return changes


def feature_status(features, jobs):
    byid = {j["id"]: j for j in jobs["jobs"]}
    changes = []
    for feature in features["features"]:
        if not feature.get("approved") or not feature.get("jobs") or feature.get("state") in {
            "FINALIZED", "CANCEL_REQUESTED", "BLOCKED"
        }:
            continue
        children = [byid.get(jid) for jid in feature["jobs"]]
        if any(x is None for x in children):
            feature["state"] = "BLOCKED"
            feature["blocker"] = "Missing child job record"
            continue
        statuses = {x["state"] for x in children}
        if all(x["state"] in ("VERIFIED", "ACCEPTED") for x in children):
            new_state = "OWNER_ACCEPTED" if feature.get("owner_accept_requested") else "READY_FOR_OWNER"
            # A finalization label alone cannot create a merge, asset, or canon publication.
            if (new_state == "OWNER_ACCEPTED" and feature.get("owner_finalize_requested")
                    and feature.get("finalization_evidence", {}).get("verified") is True
                    and feature["finalization_evidence"].get("artifact_ref")):
                new_state = "FINALIZED"
        elif any(x.get("qa_findings") for x in children):
            new_state = "REWORK"
        elif any(x["kind"] == "review" and x["state"] in
                 ("READY", "RESERVED", "RUNNING", "SUBMITTED", "REVIEW", "CHECKPOINTED")
                 for x in children):
            new_state = "QA" if any(x["state"] != "READY" for x in children if x["kind"] == "review") else "PRODUCTION"
        else:
            new_state = "PRODUCTION"
        if feature.get("state") != new_state:
            changes.append({"feature": feature["id"], "from": feature.get("state"), "to": new_state})
            feature["state"] = new_state
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("admit", "adjudicate", "status", "validate"))
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--offline", action="store_true", help="No GitHub proof; QA remains pending")
    args = parser.parse_args(argv)
    features = load(args.root, "features.json")
    jobs = load(args.root, "jobs.json")
    slots = load(args.root, "slots.json")
    config = load(args.root, "config.json")
    output = {}
    if args.command == "admit":
        jobs, output = admit(args.root, features, jobs, slots, config)
    elif args.command == "adjudicate":
        gh = None if args.offline else GitHub()
        output = {"reviews": adjudicate(jobs, gh=gh)}
    elif args.command == "status":
        output = {"features": feature_status(features, jobs)}
    else:
        output = {"feature_count": len(features["features"]),
                  "job_count": len(jobs["jobs"])}
    errors = studio.validate(jobs, slots, config)
    if errors:
        raise SystemExit("INVALID RUNTIME: " + "; ".join(errors))
    if args.write:
        save(args.root, "jobs.json", jobs)
        save(args.root, "features.json", features)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
