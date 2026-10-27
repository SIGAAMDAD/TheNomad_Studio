#!/usr/bin/env python3
"""Architecture-change governance for source jobs (no writes or Work calls).

A contributor needing a new contract must open a PROPOSAL PR in the owning
SOURCE repository. Architecture Enforcer reviews and blocks source work until
the owner accepts the exact proposal SHA after independent review.

Metadata evidence is necessary but NOT proof of GitHub authorization. The
director/worker must fetch the actual source PR, reviews, owner identity and
accepted branch/commit via authenticated GitHub readback before applying
a stored receipt. This module deliberately performs no network calls.
"""
import json
import pathlib
import re
import source_targets

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)$")
OWNER_LOGINS = frozenset(("SIGAAMDAD",))
IMPACTS = frozenset(("none", "change"))
ENFORCER = "architecture_enforcer"


def read_policy(path=None):
    p = path or ROOT / "studio" / "workforce" / "architecture_policy.v1.json"
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def expected_proposal_branch(job_id):
    stem = source_targets.expected_work_branch(job_id)
    return "studio/arch/" + job_id if stem else None


def proposal_contract(job, proposal):
    """Validate a real source-repo proposal PR identity (not approval)."""
    if not isinstance(proposal, dict):
        return "ARCHITECTURE_PROPOSAL_PR_REQUIRED"
    repo = job.get("repository")
    routes = source_targets.read_routes()
    if source_targets.validate_source_job(job, routes):
        return "ARCHITECTURE_SOURCE_JOB_INVALID"
    url = proposal.get("pr_url")
    found = URL_RE.fullmatch(url) if isinstance(url, str) else None
    if not found or found.group(1) != repo:
        return "ARCHITECTURE_PROPOSAL_MUST_BE_SOURCE_PR"
    if proposal.get("repository") != repo or proposal.get("base_branch") != job.get("branch"):
        return "ARCHITECTURE_PROPOSAL_TARGET_MISMATCH"
    if proposal.get("head_branch") != expected_proposal_branch(job.get("id")):
        return "ARCHITECTURE_PROPOSAL_BRANCH_MISMATCH"
    if proposal.get("pr_number") != int(found.group(2)):
        return "ARCHITECTURE_PROPOSAL_NUMBER_MISMATCH"
    head_sha = proposal.get("head_sha")
    if not isinstance(head_sha, str) or not SHA_RE.fullmatch(head_sha):
        return "ARCHITECTURE_PROPOSAL_SHA_REQUIRED"
    if proposal.get("author") in OWNER_LOGINS and proposal.get("reviewer") == proposal.get("author"):
        return "ARCHITECTURE_NO_SELF_APPROVAL"
    return None


def verify_owner_readback(job, proposal, github_pr, reviews):
    """Validate fields obtained separately from authenticated GitHub reads.

    Called by a trusted controller step after actual fetch; synthetic job
    metadata must NEVER be treated as authenticated readback.
    """
    reason = proposal_contract(job, proposal)
    if reason:
        return reason
    if not isinstance(github_pr, dict) or not isinstance(reviews, list):
        return "ARCHITECTURE_GITHUB_READBACK_REQUIRED"
    head = github_pr.get("head") or {}
    base = github_pr.get("base") or {}
    if (github_pr.get("number") != proposal["pr_number"] or
            (base.get("repo") or {}).get("full_name") != job["repository"] or
            (head.get("repo") or {}).get("full_name") != job["repository"] or
            base.get("ref") != job["branch"] or
            head.get("ref") != proposal["head_branch"] or
            head.get("sha") != proposal["head_sha"]):
        return "ARCHITECTURE_PR_READBACK_MISMATCH"
    # Owner must explicitly approve and merge into the source target.
    if not github_pr.get("merged") or not github_pr.get("merged_at"):
        return "ARCHITECTURE_OWNER_MERGE_REQUIRED"
    merger = (github_pr.get("merged_by") or {}).get("login")
    if merger not in OWNER_LOGINS:
        return "ARCHITECTURE_OWNER_MERGE_REQUIRED"
    valid_approvals = [
        review for review in reviews
        if review.get("state") == "APPROVED" and
        (review.get("user") or {}).get("login") in OWNER_LOGINS and
        review.get("commit_id") == proposal["head_sha"] and
        (review.get("user") or {}).get("login") != (github_pr.get("user") or {}).get("login")
    ]
    if not valid_approvals:
        return "ARCHITECTURE_EXACT_SHA_OWNER_REVIEW_REQUIRED"
    return None


def gate(job):
    """Fail closed for unknown impact or unverified architecture change.

    An approved receipt is only valid after authenticated PR/review readback
    has been persisted by the trusted owner-gated controller.
    """
    impact = job.get("architecture_impact")
    if impact not in IMPACTS:
        return "ARCHITECTURE_IMPACT_UNCLASSIFIED"
    review = job.get("architecture_review")
    if not isinstance(review, dict) or review.get("reviewer_role") != ENFORCER:
        return "ARCHITECTURE_ENFORCER_REVIEW_REQUIRED"
    if review.get("reviewed_baseline_sha") != job.get("baseline_sha"):
        return "ARCHITECTURE_REVIEW_BASELINE_STALE"
    if impact == "none":
        if review.get("verdict") != "NO_CHANGE":
            return "ARCHITECTURE_ENFORCER_CLEARANCE_REQUIRED"
        return None
    if review.get("verdict") != "PROPOSAL_REQUIRED":
        return "ARCHITECTURE_PROPOSAL_REVIEW_REQUIRED"
    proposal = job.get("architecture_proposal")
    issue = proposal_contract(job, proposal)
    if issue:
        return issue
    receipt = job.get("architecture_acceptance")
    if not isinstance(receipt, dict) or receipt.get("status") != "ACCEPTED":
        return "ARCHITECTURE_OWNER_ACCEPTANCE_REQUIRED"
    if (receipt.get("proposal_pr_url") != proposal.get("pr_url") or
            receipt.get("reviewed_head_sha") != proposal.get("head_sha") or
            receipt.get("accepted_source_baseline_sha") != job.get("baseline_sha") or
            receipt.get("approved_by") not in OWNER_LOGINS or
            receipt.get("verification_source") != "authenticated_github_pr_review_and_merge_readback" or
            not isinstance(receipt.get("merged_commit_sha"), str) or
            not SHA_RE.fullmatch(receipt["merged_commit_sha"])):
        return "ARCHITECTURE_ACCEPTANCE_READBACK_MISMATCH"
    # Receipt fields are a local cache. Consumers must still verify their
    # source and the exact current GitHub PR on work execution.
    return None


def classification_template(job, impact):
    """A safe, deterministic source job amendment; never mutates input."""
    if impact not in IMPACTS:
        raise ValueError("Unknown architecture impact")
    return {
        "architecture_impact": impact,
        "architecture_review": {
            "reviewer_role": ENFORCER,
            "reviewed_baseline_sha": job.get("baseline_sha"),
            "verdict": "NO_CHANGE" if impact == "none" else "PROPOSAL_REQUIRED"
        }
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "proposal", "validate-policy"))
    parser.add_argument("--job", type=pathlib.Path)
    args = parser.parse_args()
    policy = read_policy()
    if args.action == "validate-policy":
        assert policy["schema_version"] == 1
        assert policy["gates"]["pending_proposals_block_source_writes"] is True
        print(json.dumps({"status": "VALID", "enforcer": policy["enforcer"]["title"],
                          "separate_active_slot": False}))
        return 0
    if not args.job:
        parser.error("--job required")
    job = json.loads(args.job.read_text(encoding="utf-8"))
    if args.action == "proposal":
        print(json.dumps({
            "repository": job.get("repository"),
            "base": job.get("branch"),
            "head": expected_proposal_branch(job.get("id")),
            "document_path": "docs/architecture/proposals/" + str(job.get("id")) + ".md",
            "template_in_hub": "studio/workforce/ARCHITECTURE_PROPOSAL_TEMPLATE.md",
            "action": "OPEN_DRAFT_SOURCE_PULL_REQUEST",
            "owner_approval_required": True}, indent=2))
        return 0
    result = gate(job)
    print(json.dumps({"status": "BLOCKED" if result else "CLEARED",
                      "reason": result, "job_id": job.get("id")}, indent=2))
    return 0 if result is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
