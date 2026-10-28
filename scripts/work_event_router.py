#!/usr/bin/env python3
"""Non-spending event-worker router for The Nomad Studio.

Reads a reviewed 174-role hierarchy and optionally a verified runtime job manifest.
Produces a routing decision ONLY. It never invokes Work, buys credits, changes
scheduled tasks, grants source permission, writes a repository, or merges a PR.
"""
import argparse
import json
import pathlib
import re
import source_targets
import architecture_enforcer
import studio_work_orders

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROLE_ID = re.compile(r"^rpg-[a-z0-9-]+$")
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
SHA = re.compile(r"^[0-9a-fA-F]{40}$")
ALLOWED_PR_ACTIONS = {"opened", "reopened", "ready_for_review", "synchronize", "labeled"}
ALLOWED_EVENT_TYPES = {"pull_request"}


def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def validate_hierarchy(policy):
    errors = []
    if policy.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    profiles = policy.get("profiles", {})
    if set(profiles) != {"W01", "W02", "W03", "W04", "W05", "W06"}:
        errors.append("expected exactly W01..W05 Work profiles")
    if any(p.get("model") == "GPT-6 Astra" for p in profiles.values()):
        errors.append("Astra cannot be a default profile")
    capacity = policy.get("capacity", {})
    active = capacity.get("scheduled_slots_proposed_active", []) + capacity.get("work_slots_proposed_active", [])
    if len(active) != len(set(active)) or len(active) > 10:
        errors.append("proposed active task count must be unique and <=10")
    controls = policy.get("controls", {})
    for key in ("auto_spend_enabled", "auto_source_write_enabled", "work_event_triggers_installed"):
        if controls.get(key) is not False:
            errors.append(key + " must remain disabled in draft")
    roles = policy.get("roles", [])
    if len(roles) != 174 or len(set(r.get("id") for r in roles)) != 174:
        errors.append("exactly 174 unique role IDs required")
    byid = {r.get("id"): r for r in roles}
    for role in roles:
        rid = role.get("id", "")
        if not isinstance(rid, str) or not ROLE_ID.fullmatch(rid):
            errors.append("invalid role ID: " + str(rid))
            continue
        if role.get("scheduled_slot") not in {f"S{i:02d}" for i in range(1, 10)} | {"S00"}:
            errors.append(rid + ": invalid scheduled slot")
        if role.get("review_slot") not in {"S08", "S09"}:
            errors.append(rid + ": invalid review slot")
        if role.get("reports_to") != "HUMAN_OWNER" and role.get("reports_to") not in byid:
            errors.append(rid + ": missing reporting lead")
        if role.get("department") == "Engineering" and not role.get("luna_boilerplate_and_tests"):
            errors.append(rid + ": junior boilerplate policy missing")
    for rid in byid:
        cursor, seen = rid, set()
        while cursor != "HUMAN_OWNER":
            if cursor in seen:
                errors.append(rid + ": management cycle")
                break
            seen.add(cursor)
            parent = byid.get(cursor, {}).get("reports_to")
            if not parent:
                errors.append(rid + ": broken management chain")
                break
            cursor = parent
    stages = policy.get("stages", [])
    if {s.get("id") for s in stages} != {"alpha", "implement", "debug", "integrate", "qa", "escalate"}:
        errors.append("all five stages are required")
    if any(s.get("profile") not in profiles for s in stages):
        errors.append("stage references unknown profile")
    if policy.get("capacity", {}).get("max_independent_reviewers") != 1:
        errors.append("bootstrap requires an independent reviewer")
    return errors


def get_labels(pr):
    return {x.get("name") for x in pr.get("labels", []) if isinstance(x, dict) and isinstance(x.get("name"), str)}


def extract_job_id(pr):
    body = pr.get("body") or ""
    found = re.search(r"(?m)^Studio-Job:\s*([A-Za-z0-9_-]+)\s*$", body)
    return found.group(1) if found else None


def route_event(event, policy, runtime_jobs=None, config=None):
    """Produce a safe candidate or explicit block; never launch a model."""
    kind = event.get("event_name", "pull_request")
    action = event.get("action")
    pr = event.get("pull_request") or {}
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    sha = head.get("sha")
    repo = (base.get("repo") or {}).get("full_name")
    # Hub work-order PRs carry only controller metadata; source writes stay
    # in the actual repository recorded in the authenticated job manifest.
    if (repo == "SIGAAMDAD/TheNomad_Studio" and
            isinstance(head.get("ref"), str) and
            (head["ref"].startswith("studio/workorders/") or "Studio-Stage:" in (pr.get("body") or ""))):
        return studio_work_orders.route_work_order(event, policy, runtime_jobs, config)
    result = {"status": "IGNORED", "event": kind, "action": action,
              "repo": repo, "pr_number": pr.get("number") or event.get("number"),
              "head_sha": sha, "model_invoked": False, "credits_spent": False}
    if kind not in ALLOWED_EVENT_TYPES or action not in ALLOWED_PR_ACTIONS:
        result["reason"] = "UNSUPPORTED_EVENT"
        return result
    if not isinstance(sha, str) or not SHA.fullmatch(sha) or not isinstance(repo, str):
        result["reason"] = "UNVERIFIED_SOURCE_REF"
        return result
    if pr.get("draft", True):
        result["reason"] = "DRAFT_PR"
        return result
    labels = get_labels(pr)
    matching = [s for s in policy["stages"] if set(s.get("required_labels", [])).issubset(labels)]
    if len(matching) != 1:
        result["reason"] = "EXACTLY_ONE_WORK_STAGE_REQUIRED"
        return result
    stage = matching[0]
    result.update({"stage": stage["id"], "profile": stage["profile"],
                   "model": policy["profiles"][stage["profile"]]["model"]})
    jid = extract_job_id(pr)
    if not jid or not JOB_ID.fullmatch(jid):
        result.update({"status": "BLOCKED", "reason": "MISSING_STUDIO_JOB_TRAILER"})
        return result
    result["job_id"] = jid
    if not isinstance(runtime_jobs, dict) or not isinstance(runtime_jobs.get("jobs"), list):
        result.update({"status": "BLOCKED", "reason": "NO_AUTHORITATIVE_RUNTIME_JOB"})
        return result
    matches = [j for j in runtime_jobs["jobs"] if j.get("id") == jid]
    if len(matches) != 1 or matches[0].get("approved") is not True:
        result.update({"status": "BLOCKED", "reason": "JOB_NOT_APPROVED_OR_MISSING"})
        return result
    job = matches[0]
    roles = {x["id"]: x for x in policy["roles"]}
    role = roles.get(job.get("role"))
    if not role:
        result.update({"status": "BLOCKED", "reason": "UNKNOWN_SPECIALIST_ROLE"})
        return result
    source_job = job
    if stage["id"] == "qa":
        subject = next((x for x in runtime_jobs["jobs"] if x.get("id") == job.get("subject_job")), None)
        source_job = subject
        if (job.get("kind") != "review" or subject is None or
                subject.get("repository") != repo or subject.get("state") not in
                {"SUBMITTED", "REVIEW", "VERIFIED"} or subject.get("final_commit_sha") != sha):
            result.update({"status": "BLOCKED", "reason": "EXACT_SUBJECT_SHA_NOT_READY_FOR_REVIEW"})
            return result
        if subject.get("submitted_by_slot") == job.get("slot") and job.get("slot"):
            result.update({"status": "BLOCKED", "reason": "AUTHOR_CANNOT_SELF_REVIEW"})
            return result
    architecture_issue = architecture_enforcer.gate(source_job)
    if architecture_issue:
        result.update({"status": "BLOCKED", "reason": architecture_issue})
        return result
    # Every source-producing/technical-QA PR belongs to the actual source
    # repository, never to this control-plane repository. Same-repo head
    # and the explicitly selected target branch are mandatory.
    routes = source_targets.read_routes()
    mismatch = source_targets.validate_source_pr(pr, source_job, routes)
    if mismatch:
        result.update({"status": "BLOCKED", "reason": mismatch})
        return result
    result["source_repository"] = source_job["repository"]
    result["target_branch"] = source_job["branch"]
    result["work_branch"] = source_job["work_branch"]
    if stage["id"] in {"alpha", "implement"} and job.get("kind") != "code":
        result.update({"status": "BLOCKED", "reason": "IMPLEMENTATION_REQUIRES_CODE_JOB"})
        return result
    if stage["id"] == "integrate" and job.get("kind") != "integration":
        result.update({"status": "BLOCKED", "reason": "INTEGRATION_REQUIRES_OWN_JOB"})
        return result
    if stage["id"] in {"alpha", "implement", "debug", "integrate", "escalate"}:
        if job.get("kind") not in {"code", "integration"}:
            result.update({"status": "BLOCKED", "reason": "SOURCE_JOB_REQUIRED"})
            return result
        if not isinstance(job.get("baseline_sha"), str) or not SHA.fullmatch(job["baseline_sha"]):
            result.update({"status": "BLOCKED", "reason": "SOURCE_BASELINE_MISSING"})
            return result
    if stage["id"] == "alpha":
        if not role.get("luna_boilerplate_and_tests"):
            result.update({"status": "BLOCKED", "reason": "LUNA_NOT_AUTHORIZED_FOR_ROLE"})
            return result
        if not role.get("luna_bounded_alpha") and "studio:scope:boilerplate" not in labels:
            result.update({"status": "BLOCKED", "reason": "HIGH_RISK_ALPHA_REQUIRES_BOILERPLATE_SCOPE"})
            return result
    if stage["id"] == "implement":
        if job.get("kind") != "code" or role.get("department") != "Engineering":
            result.update({"status": "BLOCKED", "reason": "MIDLEVEL_IMPLEMENTATION_REQUIRES_ENGINEERING_CODE_JOB"})
            return result
        if not role.get("luna_bounded_alpha") and "studio:scope:boilerplate" not in labels:
            result.update({"status": "BLOCKED", "reason": "HIGH_RISK_IMPLEMENTATION_REQUIRES_SENIOR"})
            return result
    if stage["id"] == "debug" and "studio:evidence:ci-failed" not in labels:
        result.update({"status": "BLOCKED", "reason": "VERIFIED_FAILURE_EVIDENCE_REQUIRED"})
        return result
    if stage["id"] == "integrate" and "studio:evidence:upstream-verified" not in labels:
        result.update({"status": "BLOCKED", "reason": "INDEPENDENT_UPSTREAM_VERIFICATION_REQUIRED"})
        return result
    if stage["id"] == "qa":
        if job.get("kind") != "review":
            result.update({"status": "BLOCKED", "reason": "INDEPENDENT_REVIEW_JOB_REQUIRED"})
            return result
        if not job.get("subject_job"):
            result.update({"status": "BLOCKED", "reason": "REVIEW_SUBJECT_MISSING"})
            return result
    if stage["id"] == "escalate" and "studio:owner:escalated" not in labels:
        result.update({"status": "BLOCKED", "reason": "OWNER_ESCALATION_REQUIRED"})
        return result
    if not isinstance(config, dict) or config.get("auto_ai_enabled") is not True:
        result.update({"status": "BLOCKED", "reason": "AUTO_AI_DISABLED"})
        return result
    if not source_targets.allowed_in_runtime(source_job, config):
        result.update({"status": "BLOCKED", "reason": "SOURCE_NOT_IN_LIVE_ALLOWLIST"})
        return result
    if job.get("kind") in {"code", "integration"} and config.get("source_writes_enabled") is not True:
        result.update({"status": "BLOCKED", "reason": "SOURCE_WRITES_DISABLED"})
        return result
    if policy["controls"].get("auto_spend_enabled") is not True:
        result.update({"status": "BLOCKED", "reason": "NO_SPEND_AUTHORIZATION"})
        return result
    # Legacy flags cannot restore a removed event-triggered AI route.
    result.update({"status": "BLOCKED", "reason": "MANUAL_CHAT_ONLY"})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "route"))
    parser.add_argument("--policy", type=pathlib.Path,
                        default=ROOT / "studio/workforce/hierarchy.v2.json")
    parser.add_argument("--event", type=pathlib.Path)
    parser.add_argument("--jobs", type=pathlib.Path)
    parser.add_argument("--config", type=pathlib.Path)
    args = parser.parse_args(argv)
    policy = read_json(args.policy)
    problems = validate_hierarchy(policy)
    if problems:
        print(json.dumps({"status": "INVALID", "errors": problems}, indent=2))
        return 1
    if args.command == "validate":
        print(json.dumps({"status": "VALID", "roles": len(policy["roles"]),
                          "profiles": sorted(policy["profiles"]), "automatic_spend": False}))
        return 0
    if not args.event:
        parser.error("route requires --event")
    result = route_event(read_json(args.event), policy,
                         read_json(args.jobs) if args.jobs else None,
                         read_json(args.config) if args.config else None)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
