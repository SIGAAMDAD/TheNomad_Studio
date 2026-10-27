#!/usr/bin/env python3
"""Verify private source-repository CI metadata and archive an exact-SHA receipt.

Accepts an untrusted GitHub repository_dispatch candidate. Independently reads
the source workflow run, jobs and artifact index with a separately scoped token.
Never edits source, changes Studio runtime, marks QA PASS or invokes models.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

HUB = "SIGAAMDAD/TheNomad_Studio"
SHA = re.compile(r"[a-fA-F0-9]{40}\Z")
JOB_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,79}\Z")
SOURCE_REPO = re.compile(r"SIGAAMDAD/[A-Za-z0-9_.-]{1,90}\Z")
CONCLUSIONS = frozenset(("success", "failure", "cancelled", "timed_out",
                         "action_required", "neutral", "skipped"))
TRIGGERS = frozenset(("push", "pull_request", "workflow_dispatch", "merge_group"))
PAYLOAD_KEYS = frozenset(("schema_version", "job_id", "source_repo", "source_sha",
                          "workflow_run_id"))


class EvidenceError(ValueError):
    """Failure code only; untrusted payloads are never included."""


def strict_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise EvidenceError("DUPLICATE_FIELD")
        out[k] = v
    return out


def read_json(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source, object_pairs_hook=strict_pairs)


def github_get(token, url):
    if not isinstance(token, str) or not token.strip():
        raise EvidenceError("SOURCE_AUDIT_CREDENTIAL_MISSING")
    if not url.startswith("https://api.github.com/repos/SIGAAMDAD/"):
        raise EvidenceError("UNAUTHORIZED_API_HOST")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "the-nomad-studio-ci-evidence",
    })
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def parse_candidate(event):
    if not isinstance(event, dict) or event.get("action") != "studio-source-ci-v1":
        raise EvidenceError("UNSUPPORTED_DISPATCH")
    if (event.get("repository") or {}).get("full_name") != HUB:
        raise EvidenceError("UNAUTHORIZED_DISPATCH_DESTINATION")
    candidate = event.get("client_payload")
    if not isinstance(candidate, dict) or set(candidate) != PAYLOAD_KEYS:
        raise EvidenceError("INVALID_CI_CANDIDATE_FIELDS")
    if candidate["schema_version"] != 1:
        raise EvidenceError("UNKNOWN_CI_CANDIDATE_VERSION")
    if not isinstance(candidate["job_id"], str) or not JOB_ID.fullmatch(candidate["job_id"]):
        raise EvidenceError("INVALID_JOB_ID")
    if (not isinstance(candidate["source_repo"], str)
        or not SOURCE_REPO.fullmatch(candidate["source_repo"])):
        raise EvidenceError("INVALID_SOURCE_REPO")
    if not isinstance(candidate["source_sha"], str) or not SHA.fullmatch(candidate["source_sha"]):
        raise EvidenceError("INVALID_CI_SHA")
    if type(candidate["workflow_run_id"]) is not int or candidate["workflow_run_id"] <= 0:
        raise EvidenceError("INVALID_RUN_ID")
    return candidate


def validate_job(candidate, jobs, config):
    if (not isinstance(jobs, dict) or not isinstance(jobs.get("jobs"), list)
        or not isinstance(config, dict) or not isinstance(config.get("repos"), list)):
        raise EvidenceError("RUNTIME_UNAVAILABLE")
    allowed_repos = {item.get("repo") for item in config["repos"] if isinstance(item, dict)}
    if candidate["source_repo"] not in allowed_repos:
        raise EvidenceError("SOURCE_NOT_REGISTERED")
    matches = [job for job in jobs["jobs"] if isinstance(job, dict)
               and job.get("id") == candidate["job_id"]]
    if len(matches) != 1:
        raise EvidenceError("NO_UNIQUE_SOURCE_JOB")
    job = matches[0]
    if job.get("approved") is not True or job.get("kind") not in ("code", "integration"):
        raise EvidenceError("SOURCE_JOB_NOT_APPROVED")
    if job.get("repository") != candidate["source_repo"]:
        raise EvidenceError("CI_REPOSITORY_JOB_MISMATCH")
    expected_branch = job.get("work_branch")
    if not isinstance(expected_branch, str) or not expected_branch.strip():
        raise EvidenceError("SOURCE_WORK_BRANCH_MISSING")
    if job.get("state") in ("CANCELLED", "FAILED"):
        raise EvidenceError("SOURCE_JOB_INACTIVE")
    return job


def source_api(repo, run_id, suffix=""):
    if not SOURCE_REPO.fullmatch(repo) or type(run_id) is not int or run_id <= 0:
        raise EvidenceError("INVALID_SOURCE_API_ARGUMENT")
    return f"https://api.github.com/repos/{repo}/actions/runs/{run_id}{suffix}"


def source_run_evidence(candidate, runtime_jobs, config, get):
    job = validate_job(candidate, runtime_jobs, config)
    repo, run_id = candidate["source_repo"], candidate["workflow_run_id"]
    run = get(source_api(repo, run_id))
    if not isinstance(run, dict):
        raise EvidenceError("INVALID_WORKFLOW_RUN")
    if (run.get("id") != run_id
        or run.get("head_sha") != candidate["source_sha"]
        or run.get("head_branch") != job["work_branch"]
        or (run.get("repository") or {}).get("full_name") != repo
        or run.get("event") not in TRIGGERS):
        raise EvidenceError("CI_SHA_BRANCH_OR_REPO_MISMATCH")
    if run.get("status") != "completed" or run.get("conclusion") not in CONCLUSIONS:
        raise EvidenceError("CI_WORKFLOW_NOT_FINAL")
    if type(run.get("run_attempt")) is not int or run["run_attempt"] < 1:
        raise EvidenceError("INVALID_RUN_ATTEMPT")
    jobs_url = source_api(repo, run_id, "/jobs?per_page=100")
    ci_jobs = get(jobs_url)
    if (not isinstance(ci_jobs, dict) or type(ci_jobs.get("total_count")) is not int
        or not isinstance(ci_jobs.get("jobs"), list)
        or len(ci_jobs["jobs"]) != ci_jobs["total_count"]
        or ci_jobs["total_count"] > 100):
        raise EvidenceError("INCOMPLETE_CI_JOBS")
    checked_jobs = []
    for item in ci_jobs["jobs"]:
        if (not isinstance(item, dict) or type(item.get("id")) is not int
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("conclusion"), str)
            or item["conclusion"] not in CONCLUSIONS):
            raise EvidenceError("INVALID_CI_JOB_METADATA")
        checked_jobs.append({"id": item["id"], "name": item["name"],
                             "conclusion": item["conclusion"]})
    artifact_response = get(source_api(repo, run_id, "/artifacts?per_page=100"))
    if (not isinstance(artifact_response, dict)
        or type(artifact_response.get("total_count")) is not int
        or not isinstance(artifact_response.get("artifacts"), list)
        or len(artifact_response["artifacts"]) != artifact_response["total_count"]
        or artifact_response["total_count"] > 100):
        raise EvidenceError("INCOMPLETE_ARTIFACT_INDEX")
    artifacts = []
    for item in artifact_response["artifacts"]:
        if (not isinstance(item, dict) or type(item.get("id")) is not int
            or not isinstance(item.get("name"), str)
            or type(item.get("size_in_bytes")) is not int
            or type(item.get("expired")) is not bool):
            raise EvidenceError("INVALID_CI_ARTIFACT_METADATA")
        artifacts.append({
            "id": item["id"], "name": item["name"],
            "size_bytes": item["size_in_bytes"], "expired": item["expired"],
            "digest_claimed_by_github": item.get("digest"),
            "payload_content_hash_verified": False,
        })
    event_string = f"{repo}:{run_id}:{run['run_attempt']}:{candidate['source_sha']}"
    receipt_id = "ci-" + hashlib.sha256(event_string.encode("ascii")).hexdigest()[:32]
    final_sha = job.get("final_commit_sha")
    return {
        "schema_version": 1, "receipt_id": receipt_id,
        "job_id": job["id"], "source_repo": repo,
        "source_sha": candidate["source_sha"],
        "work_branch": job["work_branch"],
        "source_run_id": run_id, "source_run_attempt": run["run_attempt"],
        "source_run_url": f"https://github.com/{repo}/actions/runs/{run_id}",
        "run_conclusion": run["conclusion"],
        "run_event": run["event"], "run_completed_at": run.get("updated_at"),
        "source_job_final_sha_match": bool(final_sha and final_sha == candidate["source_sha"]),
        "source_writes_enabled_at_observation": config.get("source_writes_enabled") is True,
        "jobs": checked_jobs, "artifacts": artifacts,
        "source_run_metadata_verified": True,
        "artifacts_content_verified": False,
        "independent_qa_verdict": "NOT_RUN",
        "owner_accepted": False,
        "runtime_promotion_authorized": False,
    }


def commit_record(root, record):
    repo_short = record["source_repo"].split("/", 1)[1]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,90}", repo_short):
        raise EvidenceError("INVALID_SOURCE_DIRECTORY")
    folder = pathlib.Path(root) / "evidence" / "source_ci" / repo_short
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (record["receipt_id"] + ".json")
    content = json.dumps(record, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise EvidenceError("CI_RECEIPT_COLLISION")
        return False, str(path)
    with path.open("x", encoding="utf-8") as out:
        out.write(content)
    return True, str(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--comms-root", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    try:
        candidate = parse_candidate(read_json(args.event))
        jobs, config = read_json(args.jobs), read_json(args.config)
        result = source_run_evidence(candidate, jobs, config,
                                     lambda url: github_get(args.token, url))
        created, path = commit_record(args.comms_root, result)
        print(json.dumps({"status": "VERIFIED_CI_METADATA_ONLY",
                          "receipt_id": result["receipt_id"], "created": created,
                          "record_path": path, "run_conclusion": result["run_conclusion"],
                          "source_final_sha_match": result["source_job_final_sha_match"]},
                         sort_keys=True))
        return 0
    except (EvidenceError, ValueError, OSError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "BLOCKED", "code": type(exc).__name__}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
