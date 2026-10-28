#!/usr/bin/env python3
"""Safe, non-spending router for Studio HUB work-order pull requests.

The source-producing job, its target branch and work branch all stay in
the named source repository. This module never commits source or runs Work.
This legacy entrypoint is archive-only. All worker launches are manual.
"""
import re
import source_targets
import architecture_enforcer

HUB = "SIGAAMDAD/TheNomad_Studio"
SHA = re.compile(r"^[0-9a-fA-F]{40}$")
JOB = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,79}$")
TRAILER = re.compile(r"(?m)^([A-Za-z-]+):[ \t]*([^\r\n]+)[ \t]*$")
ACTIONS = frozenset(("opened", "reopened", "ready_for_review", "synchronize", "labeled"))
STAGES = {"alpha": "W01", "implement": "W02", "debug": "W03",
          "integrate": "W04", "qa": "W05"}
EXTRA_EVIDENCE = {"debug": "studio:evidence:ci-failed",
                  "integrate": "studio:evidence:upstream-verified"}


def trailer_dict(body):
    found = {}
    for key, value in TRAILER.findall(body or ""):
        if key.startswith("Studio-"):
            if key in found:
                return None
            found[key] = value.strip()
    return found


def route_work_order(event, policy, runtime_jobs=None, config=None):
    pr = event.get("pull_request") or {}
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    result = {
        "status": "IGNORED", "kind": "studio_work_order",
        "model_invoked": False, "credits_spent": False,
        "pr_number": pr.get("number") or event.get("number"),
        "hub": HUB
    }
    if (event.get("event_name", "pull_request") != "pull_request" or
            event.get("action") not in ACTIONS):
        result["reason"] = "UNSUPPORTED_EVENT"
        return result
    if ((base.get("repo") or {}).get("full_name") != HUB or
            (head.get("repo") or {}).get("full_name") != HUB or
            base.get("ref") != "main"):
        result.update(status="BLOCKED", reason="HUB_WORK_ORDER_PR_REPOSITORY_MISMATCH")
        return result
    head_sha = head.get("sha")
    if not isinstance(head_sha, str) or not SHA.fullmatch(head_sha):
        result.update(status="BLOCKED", reason="HUB_WORK_ORDER_HEAD_SHA_MISSING")
        return result
    if pr.get("draft", True):
        result["reason"] = "DRAFT_PR"
        return result
    if (pr.get("user") or {}).get("login") != "SIGAAMDAD":
        result.update(status="BLOCKED", reason="WORK_ORDER_MUST_BE_CREATED_BY_OWNER")
        return result
    trailers = trailer_dict(pr.get("body"))
    if trailers is None:
        result.update(status="BLOCKED", reason="DUPLICATE_WORK_ORDER_TRAILERS")
        return result
    job_id, stage = trailers.get("Studio-Job"), trailers.get("Studio-Stage")
    if not job_id or not JOB.fullmatch(job_id) or stage not in STAGES:
        result.update(status="BLOCKED", reason="WORK_ORDER_JOB_OR_STAGE_MISSING")
        return result
    result.update(job_id=job_id, stage=stage, profile=STAGES[stage],
                  model=policy["profiles"][STAGES[stage]]["model"])
    expected_ref = f"studio/workorders/{job_id}/{stage}"
    if head.get("ref") != expected_ref:
        result.update(status="BLOCKED", reason="WORK_ORDER_BRANCH_MISMATCH")
        return result
    labels = {x.get("name") for x in pr.get("labels", []) if isinstance(x, dict)}
    stage_labels = {f"studio:work:{s}" for s in STAGES} | {"studio:work:escalate"}
    if labels & stage_labels != {f"studio:work:{stage}"}:
        result.update(status="BLOCKED", reason="EXACT_WORK_ORDER_STAGE_LABEL_REQUIRED")
        return result
    if not isinstance(runtime_jobs, dict) or not isinstance(runtime_jobs.get("jobs"), list):
        result.update(status="BLOCKED", reason="NO_LIVE_STUDIO_MANIFEST")
        return result
    matches = [j for j in runtime_jobs["jobs"] if j.get("id") == job_id]
    if len(matches) != 1 or matches[0].get("approved") is not True:
        result.update(status="BLOCKED", reason="JOB_NOT_APPROVED_OR_MISSING")
        return result
    job = matches[0]
    if stage != "qa" and job.get("state") not in ("READY", "RESERVED", "CHECKPOINTED", "REWORK"):
        result.update(status="BLOCKED", reason="WORK_ORDER_JOB_NOT_EXECUTION_READY")
        return result
    source = job
    if stage == "qa":
        source = next((j for j in runtime_jobs["jobs"]
                       if j.get("id") == job.get("subject_job")), None)
        if (job.get("kind") != "review" or source is None or
                source.get("state") not in ("SUBMITTED", "REVIEW", "VERIFIED") or
                not SHA.fullmatch(source.get("final_commit_sha", "")) or
                source.get("submitted_by_slot") == job.get("slot") and job.get("slot")):
            result.update(status="BLOCKED", reason="QA_SUBJECT_NOT_READY")
            return result
    else:
        if (stage in ("alpha", "implement") and job.get("kind") != "code" or
                stage == "integrate" and job.get("kind") != "integration" or
                stage == "debug" and job.get("kind") not in ("code", "integration")):
            result.update(status="BLOCKED", reason="WORK_ORDER_JOB_KIND_MISMATCH")
            return result
    mismatch = source_targets.validate_source_job(source, source_targets.read_routes())
    if mismatch:
        result.update(status="BLOCKED", reason=mismatch)
        return result
    baseline = source.get("baseline_sha")
    if (trailers.get("Studio-Source") != source["repository"] or
            trailers.get("Studio-Target") != source["branch"] or
            trailers.get("Studio-Work-Branch") != source["work_branch"] or
            trailers.get("Studio-Source-Baseline") != baseline):
        result.update(status="BLOCKED", reason="WORK_ORDER_SOURCE_BINDING_MISMATCH")
        return result
    if stage == "qa":
        if trailers.get("Studio-Subject-SHA") != source["final_commit_sha"]:
            result.update(status="BLOCKED", reason="WORK_ORDER_QA_COMMIT_MISMATCH")
            return result
    if not source.get("role") or source["role"] not in {r["id"] for r in policy["roles"]}:
        result.update(status="BLOCKED", reason="UNKNOWN_SPECIALIST_ROLE")
        return result
    role = next(r for r in policy["roles"] if r["id"] == source["role"])
    if stage == "alpha" and (
            not role.get("luna_boilerplate_and_tests") or
            not role.get("luna_bounded_alpha") and "studio:scope:boilerplate" not in labels):
        result.update(status="BLOCKED", reason="JUNIOR_SCOPE_EXCEEDS_CONTRACT")
        return result
    if stage == "implement" and (
            role.get("department") != "Engineering" or
            not role.get("luna_bounded_alpha") and "studio:scope:boilerplate" not in labels):
        result.update(status="BLOCKED", reason="MIDLEVEL_SCOPE_EXCEEDS_CONTRACT")
        return result
    if stage in EXTRA_EVIDENCE and EXTRA_EVIDENCE[stage] not in labels:
        result.update(status="BLOCKED", reason="STAGE_EVIDENCE_MISSING")
        return result
    issue = architecture_enforcer.gate(source)
    if issue:
        result.update(status="BLOCKED", reason=issue)
        return result
    result.update(source_repository=source["repository"], target_branch=source["branch"],
                  source_work_branch=source["work_branch"], pinned_source_sha=baseline,
                  specialist_role=source["role"], workstream=source["workstream"])
    # Do not trust PR body/label/claimed approval by itself. This router is
    # run before Work and does not establish true writer lease or owner credit.
    if not isinstance(config, dict) or config.get("auto_ai_enabled") is not True:
        result.update(status="BLOCKED", reason="AUTO_AI_DISABLED")
        return result
    if not source_targets.allowed_in_runtime(source, config):
        result.update(status="BLOCKED", reason="SOURCE_NOT_IN_LIVE_ALLOWLIST")
        return result
    if stage != "qa" and config.get("source_writes_enabled") is not True:
        result.update(status="BLOCKED", reason="SOURCE_WRITES_DISABLED")
        return result
    if policy["controls"].get("auto_spend_enabled") is not True:
        result.update(status="BLOCKED", reason="NO_SPEND_AUTHORIZATION")
        return result
    result.update(status="BLOCKED", reason="MANUAL_CHAT_ONLY")
    return result
