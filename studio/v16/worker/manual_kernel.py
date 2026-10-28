#!/usr/bin/env python3
"""Offline V1.6 manual High launcher/role integrity helper.

It never authenticates owners, grants, leases, access consent, runs AI, writes
remote state, or constructs a privileged publication intent.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
WORKER = Path(__file__).resolve().parent
ROLES = ROOT / "skills/rpg-studio-all-roles/references/roles_manifest.json"
DEPARTMENTS = ROOT / "skills/rpg-studio-all-roles/references/departments.v4.json"
PROMPTS = ROOT / "studio/v1/prompts"
PROFILE = WORKER / "PROFILES.v1.json"
REGISTRY = WORKER / "ROLE_REGISTRY.v1.json"
CONTRACT_MANIFEST = ROOT / "studio/v16/contracts/FREEZE_MANIFEST.v1.json"
SOURCE_ROUTES = ROOT / "studio/workforce/source_targets.v1.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ROLE = re.compile(r"^rpg-[a-z0-9-]+$")


class JobError(ValueError):
    """Invalid local input, not an externally authenticated access denial."""


def run_bounded_response(authenticated_port, response_id, worker_id, department,
                         execute_job, capture_resume, *, max_jobs=20, max_seconds=600):
    """Manual host entry point; the port owns service authentication and claims.

    Load the pinned role brief once before invoking this method. The callbacks
    execute local work and checkpoint actual evidence after each attempted job.
    No ordinary chat is awakened by this helper.
    """
    from studio_v16.adapters.response import run_response
    return run_response(authenticated_port, response_id, worker_id, department,
                        execute_job, capture_resume, max_jobs=max_jobs, max_seconds=max_seconds)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_registry():
    role_data = read_json(ROLES)["roles"]
    departments = read_json(DEPARTMENTS)
    names = departments["role_to_department"]
    heads = departments["departments"]
    if len(role_data) != 174 or len(names) != 174:
        raise JobError("EXPECTED_EXACTLY_174_ROLES")
    if len(heads) != 12:
        raise JobError("EXPECTED_12_DEPARTMENTS")
    seen = set()
    roles = []
    for role in sorted(role_data, key=lambda item: item["id"]):
        rid = role["id"]
        if not ROLE.fullmatch(rid) or rid in seen:
            raise JobError("INVALID_OR_DUPLICATE_ROLE")
        seen.add(rid)
        if rid not in names or names[rid] not in heads:
            raise JobError("ROLE_DEPARTMENT_MISSING: " + rid)
        path = PROMPTS / (rid + ".md")
        if not path.is_file():
            raise JobError("ROLE_LAUNCHER_MISSING: " + rid)
        source = path.read_text(encoding="utf-8")
        for expected in (rid, role["outputs"][0], role["acceptance"][0],
                         "studio/v16/worker/README.md"):
            if expected not in source:
                raise JobError("ROLE_LAUNCHER_DEGRADED: " + rid + " -> " + expected)
        dept = names[rid]
        roles.append({
            "role_id": rid,
            "title": role["title"],
            "department": dept,
            "head_role_id": heads[dept]["head_role"],
            "manual_skill_uri": "skills://" + rid,
            "manual_availability": "EXTERNAL_INSTALLED_SKILL_VERIFY_ON_LAUNCH",
            "local_launcher": path.relative_to(ROOT).as_posix(),
            "local_launcher_sha256": digest(path),
            "required_output": role["outputs"][0],
            "acceptance": role["acceptance"][0],
        })
    return {
        "schema_id": "MANUAL-ROLE-REGISTRY/1",
        "launch_mode": "USER_OPENED_MANUAL_CHAT",
        "reasoning": "HIGH",
        "role_count": 174,
        "department_count": 12,
        "roles_manifest_sha256": digest(ROLES),
        "departments_sha256": digest(DEPARTMENTS),
        "profiles_sha256": digest(PROFILE),
        "wg_a_freeze_manifest_sha256": digest(CONTRACT_MANIFEST),
        "roles": roles,
    }


def validate_registry():
    current = build_registry()
    if not REGISTRY.exists() or read_json(REGISTRY) != current:
        raise JobError("ROLE_REGISTRY_STALE_OR_MISSING; run registry --write")
    return current


def validate_owned_path(path):
    if not isinstance(path, str) or not path or len(path) > 240:
        raise JobError("INVALID_PATH")
    if (path.startswith("/") or "\\" in path or "%" in path or
            any(ord(c) < 32 or ord(c) > 126 for c in path)):
        raise JobError("INVALID_PATH")
    segments = path.split("/")
    if any(seg in ("", ".", "..") or seg.startswith(".") for seg in segments):
        raise JobError("INVALID_PATH")
    return path


def validate_job(job, registry, specialty):
    if not isinstance(job, dict):
        raise JobError("JOB_MUST_BE_OBJECT")
    if specialty not in read_json(PROFILE)["specialties"]:
        raise JobError("UNKNOWN_MANUAL_SPECIALTY")
    fields = ("job_id", "worker_id", "role_id", "source_repo", "source_branch",
              "source_base_sha", "source_archive_sha256", "owned_path_prefixes",
              "acceptance_ids", "scope")
    absent = [key for key in fields if key not in job]
    if absent:
        raise JobError("JOB_FIELDS_MISSING: " + ",".join(absent))
    if any(not isinstance(job[k], str) or not ID.fullmatch(job[k]) for k in ("job_id", "worker_id")):
        raise JobError("INVALID_JOB_OR_WORKER_ID")
    roles = {r["role_id"] for r in registry["roles"]}
    if job["role_id"] not in roles:
        raise JobError("UNKNOWN_174_ROLE_ID")
    if not isinstance(job["source_repo"], str) or not REPO.fullmatch(job["source_repo"]):
        raise JobError("INVALID_REPOSITORY")
    if not isinstance(job["source_branch"], str) or not job["source_branch"].strip():
        raise JobError("INVALID_BRANCH")
    if (not isinstance(job["source_base_sha"], str) or
            not SHA.fullmatch(job["source_base_sha"])):
        raise JobError("INVALID_BASE_SHA")
    if (not isinstance(job["source_archive_sha256"], str) or
            not SHA256.fullmatch(job["source_archive_sha256"])):
        raise JobError("INVALID_ARCHIVE_SHA256")
    if not isinstance(job["scope"], str) or len(job["scope"].strip()) < 8:
        raise JobError("UNBOUNDED_SCOPE")
    for key in ("owned_path_prefixes", "acceptance_ids"):
        if not isinstance(job[key], list) or not job[key] or len(set(map(str, job[key]))) != len(job[key]):
            raise JobError("MISSING_OR_DUPLICATE_" + key.upper())
    for path in job["owned_path_prefixes"]:
        validate_owned_path(path)
    for value in job["acceptance_ids"]:
        if not isinstance(value, str) or not ID.fullmatch(value):
            raise JobError("INVALID_ACCEPTANCE_ID")
    # A registry lookup does not authorize a source branch, job or write. An
    # exact match with recorded routes prevents accidental default-branch
    # substitution but has no gateway authority by itself.
    source = read_json(SOURCE_ROUTES)
    candidates = list_source_route_pairs(source)
    if (job["source_repo"], job["source_branch"]) not in candidates:
        # Studio control-plane maintenance jobs are separate from game routes.
        if (job["source_repo"], job["source_branch"]) != ("SIGAAMDAD/TheNomad_Studio", "main"):
            raise JobError("SOURCE_ROUTE_NOT_IN_REGISTRY")
    return job


def list_source_route_pairs(document):
    """Read the actual existing route grammar, no branch name guessing."""
    pairs = set()
    def visit(node):
        if isinstance(node, dict):
            repo = node.get("repo") or node.get("repository")
            branch = node.get("branch") or node.get("target_branch") or node.get("canonical_branch")
            if isinstance(repo, str) and isinstance(branch, str) and REPO.fullmatch(repo):
                pairs.add((repo, branch))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(document)
    return pairs


def launch_text(job, specialty, registry):
    validate_job(job, registry, specialty)
    role = next(r for r in registry["roles"] if r["role_id"] == job["role_id"])
    return "\n".join([
        "MANUAL GPT-6 HIGH WORKER — DRAFT LAUNCHER; NOT AN AUTHORIZATION",
        "Job: " + job["job_id"] + " | worker: " + job["worker_id"] + " | role: " + job["role_id"],
        "Department: " + role["department"] + " | accountable head: " + role["head_role_id"],
        "Specialty: " + specialty + " | " + read_json(PROFILE)["specialties"][specialty]["scope"],
        "Read studio/v16/worker/README.md and " + role["local_launcher"],
        "Load exact installed role skill " + role["manual_skill_uri"] + " if available; do not invent missing manuals.",
        "Source: " + job["source_repo"] + " @ " + job["source_branch"],
        "Reported baseline SHA (independent remote readback NOT established): " + job["source_base_sha"],
        "Snapshot SHA256 (verify against local bytes before editing): " + job["source_archive_sha256"],
        "Owned path prefixes: " + ", ".join(job["owned_path_prefixes"]),
        "Scope: " + job["scope"],
        "Acceptance IDs: " + ", ".join(job["acceptance_ids"]),
        "Before your first GitHub/Google Drive connector call, request explicit owner consent for the exact resource/ref/path, operation and reason. If none needed, proceed offline.",
        "For credential isolation absent: LOCAL_PATCH_ONLY, no privileged remote publication. Connector consent is NOT a Studio owner grant.",
        "Complete all feasible local work, verify owned-path diff, run tests and batch a patch plus SHA manifest.",
        "Return per-response audit report card + RUN-V16 resume capsule; no invented event/ledger/relay/QA readbacks.",
        "Independent QA stays READ_ONLY; integrators stay STAGING_ONLY; no worker has canonical merge authority.",
        "Previous response/checkpoint must be verified on resume; each new response has a distinct attempt ID.",
    ]) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="cmd", required=True)
    reg = subs.add_parser("registry", help="verify or regenerate exact role hashes")
    reg.add_argument("--write", action="store_true")
    subs.add_parser("list", help="list 174 exact role IDs, department and head")
    launch = subs.add_parser("launch", help="render local-only manual High job launcher")
    launch.add_argument("--job", required=True, type=Path)
    launch.add_argument("--role", required=True)
    launch.add_argument("--specialty", required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "registry" and args.write:
            data = build_registry()
            REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            data = validate_registry()
        if args.cmd == "registry":
            print("PASS 174 manual roles / 12 departments; registry SHA256=" + digest(REGISTRY))
        elif args.cmd == "list":
            for role in data["roles"]:
                print("\t".join((role["role_id"], role["department"], role["head_role_id"])))
        else:
            job = read_json(args.job)
            if job.get("role_id") != args.role:
                raise JobError("ROLE_MISMATCH")
            print(launch_text(job, args.specialty, data), end="")
    except (JobError, OSError, ValueError, TypeError, KeyError) as exc:
        print("BLOCKED_LOCAL_INPUT: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
