#!/usr/bin/env python3
"""Reviewed source-repository boundaries for Studio-controlled engineering.

This module validates immutable routing intent only. It performs no Git
operations, no GitHub API calls, no Work launches and no state mutation.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTES_FILE = ROOT / "studio" / "workforce" / "source_targets.v1.json"
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
JOB_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,79}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def read_routes(path=ROUTES_FILE):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def route_table(routes):
    if not isinstance(routes, dict) or routes.get("schema_version") != 1:
        raise ValueError("Invalid source routing policy schema")
    entries = routes.get("source_routes")
    if not isinstance(entries, list) or len(entries) != 5:
        raise ValueError("Exactly five reviewed source routes are required")
    hub = routes.get("control_repository")
    if hub != "SIGAAMDAD/TheNomad_Studio":
        raise ValueError("Control repository identity is incorrect")
    table = {}
    workstreams = set()
    for entry in entries:
        repo, branch = entry.get("repository"), entry.get("target_branch")
        if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
            raise ValueError("Invalid source repository")
        if not isinstance(branch, str) or not branch or branch in ("HEAD", "main/master"):
            raise ValueError("Invalid source target branch")
        if repo == hub or repo in table:
            raise ValueError("Control repository or duplicate route cannot own game source")
        workstream = entry.get("workstream")
        if not isinstance(workstream, str) or workstream in workstreams:
            raise ValueError("Source workstreams must be unique and explicit")
        workstreams.add(workstream)
        table[repo] = branch
    expected = {
        "SIGAAMDAD/TheNomad_GOAP": "main",
        "SIGAAMDAD/NomadFramework": "develop",
        "SIGAAMDAD/The_Nomad": "Nomad3D",
        "SIGAAMDAD/Terrain3D": "Terrain3D_NomadRenderer",
        "SIGAAMDAD/NomadMain": "main"
    }
    if table != expected:
        raise ValueError("Source routes differ from approved owner selection")
    approved_workstreams = {
        "goap_core": "SIGAAMDAD/TheNomad_GOAP",
        "framework_engine": "SIGAAMDAD/NomadFramework",
        "core_gameplay": "SIGAAMDAD/The_Nomad",
        "rendering_engine": "SIGAAMDAD/Terrain3D",
        "mod_gameplay": "SIGAAMDAD/NomadMain",
    }
    if {entry["workstream"]: entry["repository"] for entry in entries} != approved_workstreams:
        raise ValueError("Workstream ownership differs from approved owner graph")
    return table


def expected_work_branch(job_id):
    if not isinstance(job_id, str) or not JOB_RE.fullmatch(job_id):
        return None
    return "studio/" + job_id


def validate_source_job(job, routes, require_baseline=True):
    """Return first specific violation, otherwise None."""
    repo, base = job.get("repository"), job.get("branch")
    if repo == routes.get("control_repository"):
        return "CONTROL_REPOSITORY_FORBIDDEN_FOR_SOURCE"
    target = route_table(routes).get(repo)
    if target is None:
        return "UNRECOGNIZED_SOURCE_REPOSITORY"
    if base != target:
        return "SOURCE_TARGET_BRANCH_MISMATCH"
    workstream = job.get("workstream")
    if not isinstance(workstream, str) or not workstream:
        return "SOURCE_WORKSTREAM_REQUIRED"
    reviewed_workstream = next((x["workstream"] for x in routes["source_routes"]
                                if x["repository"] == repo), None)
    if workstream != reviewed_workstream:
        return "SOURCE_WORKSTREAM_REPOSITORY_MISMATCH"
    if workstream == "mod_gameplay" and job.get("language") != "C#":
        return "MOD_GAMEPLAY_REQUIRES_CSHARP"
    expect = expected_work_branch(job.get("id"))
    if expect is None or job.get("work_branch") != expect:
        return "SOURCE_WORK_BRANCH_MISMATCH"
    if require_baseline and (
            not isinstance(job.get("baseline_sha"), str) or
            not SHA_RE.fullmatch(job["baseline_sha"])):
        return "SOURCE_BASELINE_SHA_MISSING"
    return None


def validate_source_pr(pr, source_job, routes):
    """Require a same-repository PR from dedicated job branch to exact target."""
    problem = validate_source_job(source_job, routes)
    if problem:
        return problem
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    source_repo = source_job["repository"]
    base_repo = (base.get("repo") or {}).get("full_name")
    head_repo = (head.get("repo") or {}).get("full_name")
    if base_repo != source_repo:
        return "PR_BASE_REPOSITORY_MISMATCH"
    if base.get("ref") != source_job["branch"]:
        return "PR_BASE_BRANCH_MISMATCH"
    if head_repo != source_repo:
        return "PR_HEAD_REPOSITORY_MISMATCH"
    if head.get("ref") != source_job["work_branch"]:
        return "PR_HEAD_WORK_BRANCH_MISMATCH"
    if not isinstance(head.get("sha"), str) or not SHA_RE.fullmatch(head["sha"]):
        return "PR_HEAD_SHA_INVALID"
    return None


def allowed_in_runtime(source_job, config):
    """A route map is insufficient: the live production allowlist must agree."""
    entries = config.get("repos", []) if isinstance(config, dict) else []
    return any(isinstance(x, dict) and x.get("repo") == source_job.get("repository")
               and x.get("branch") == source_job.get("branch") for x in entries)
