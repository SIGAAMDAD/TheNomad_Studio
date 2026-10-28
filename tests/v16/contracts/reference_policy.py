"""TEST-ONLY reference oracle for AUTH-V16 boundary fixtures.

No provider connections, no credential verifier, no authority grants and no
publish capability. Runtime implementers must supply independent enforcement.
"""
from __future__ import annotations

import re


def path_error(path: str, ctx: dict) -> str | None:
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
        return "INVALID_PATH"
    if path.lower().startswith((".git/", ".github/")):
        return "PROTECTED_PATH"
    if (path.startswith("/") or path.endswith("/") or "//" in path
            or any(part in ("", ".", "..") or part.startswith(".") for part in path.split("/"))):
        return "INVALID_PATH"
    # These are trusted Git-tree-derived facts, not client declarations.
    if path in ctx.get("git_tree_links", []) or path in ctx.get("git_tree_submodules", []):
        return "INVALID_PATH"
    if path.lower().startswith((".git/", ".github/")):
        return "PROTECTED_PATH"
    # Subtree-level case folding: lowercased alias must resolve identically.
    actual_paths = ctx.get("git_tree_paths", [])
    if any(p.casefold() == path.casefold() and p != path for p in actual_paths):
        return "INVALID_PATH"
    allowed = ctx.get("owned_path_prefixes", [])
    if not any(path.startswith(prefix) and len(path) > len(prefix) for prefix in allowed):
        return "INVALID_SCOPE"
    return None


def evaluate(request: dict, ctx: dict) -> str:
    """Returns ALLOW or the FIRST stable denial code from AUTH-V16.md.

    All ctx values are an independent synthetic trusted source in conformance
    tests. Never accept them from a worker request in production.
    """
    if not ctx["gateway_online"]:
        return "GATEWAY_UNAVAILABLE"
    if ctx["auth_channel"] == "SHARED_OWNER_CONNECTOR":
        return "UNTRUSTED_CONNECTOR"
    if not ctx["credential_verified"] or request["claimed_principal_id"] != ctx["authenticated_principal_id"]:
        return "IDENTITY_UNVERIFIED"
    if not ctx["owner_grant_exists"] or request["grant_id"] != ctx["grant_id"]:
        return "ACCESS_REQUIRED"
    if not ctx["owner_decision_approved"]:
        return "OWNER_APPROVAL_REQUIRED"
    if (request["job_id"] != ctx["job_id"] or request["worker_id"] != ctx["worker_id"]
            or request["repo_id"] != ctx["repo_id"] or ctx["approved_scope_sha256"] != ctx["grant_scope_sha256"]):
        return "INVALID_SCOPE"
    if request["policy_revision"] != ctx["policy_revision"]:
        return "POLICY_REVISION_MISMATCH"
    if request["authority_revision"] != ctx["authority_revision"]:
        return "AUTHORITY_REVISION_MISMATCH"
    if ctx["revoked"]:
        return "REVOKED_GRANT"
    if not (ctx["issued_at"] <= ctx["server_now"] < ctx["expires_at"]):
        return "EXPIRED_GRANT"
    if request["assignment_epoch"] != ctx["assignment_epoch"]:
        return "STALE_ASSIGNMENT"
    if (request["lease_generation"] != ctx["lease_generation"]
            or ctx["lease_attempt_id"] != request["attempt_id"]):
        return "STALE_LEASE"
    if request["source_base_sha"] != ctx["source_base_sha"]:
        return "REBASE_REQUIRED"
    if (request["target_branch"] is None or request["target_branch"] != ctx["work_branch"]):
        return "INVALID_SCOPE"
    for path in request["target_paths"]:
        err = path_error(path, ctx)
        if err:
            return err
    if request["operation"] not in ctx["allowed_operations"]:
        return "INVALID_SCOPE"
    if request["operation"] in {"SOURCE_MERGE", "DEPLOY", "APPROVE_CANON", "APPROVE_SPEND"}:
        return "OWNER_APPROVAL_REQUIRED"
    if not ctx["qa_verified"]:
        return "QA_PENDING"
    if ctx["qa_reviewer_principal_id"] == ctx["authenticated_principal_id"]:
        return "SELF_REVIEW"
    if not ctx["budget_authorized"]:
        return "BUDGET_DENIED"
    if request["operation"] == "SOURCE_BRANCH_UPDATE" and not ctx["source_writes_enabled"]:
        return "CUTOVER_NOT_APPROVED"
    return "ALLOW"


def verify_publication(intent: dict, receipt: dict, ctx: dict) -> str:
    """Pure negative-vector matcher; not a GitHub/Drive provider or permission grant.

    Trusted ctx simulates server/provider facts, never sourced from a worker.
    """
    if not ctx["gateway_online"]:
        return "GATEWAY_UNAVAILABLE"
    if ctx["authenticated_principal_id"] != intent["principal_id"]:
        return "IDENTITY_UNVERIFIED"
    if not ctx["owner_approved"] or intent["owner_decision_ref"] != ctx["owner_decision_ref"]:
        return "OWNER_APPROVAL_REQUIRED"
    if intent["assignment_epoch"] != ctx["assignment_epoch"]:
        return "STALE_ASSIGNMENT"
    if intent["lease_generation"] != ctx["lease_generation"]:
        return "STALE_LEASE"
    if ctx["reserved_idempotency_key"] == intent["idempotency_key"] and ctx["reserved_artifact_sha256"] != intent["artifact_sha256"]:
        return "IDEMPOTENCY_CONFLICT"
    if not ctx["qa_attested"] or intent["independent_qa"]["subject_sha256"] != ctx["qa_subject_sha256"]:
        return "QA_PENDING"
    if intent["independent_qa"]["reviewer_principal_id"] == intent["principal_id"]:
        return "SELF_REVIEW"
    if intent["independent_qa"]["subject_sha256"] not in (intent["artifact_sha256"], intent["target"].get("proposed_tree_sha256"), intent["target"].get("exported_sha256")):
        return "QA_PENDING"
    target = intent["target"]
    if target["provider"] == "GITHUB":
        if target["source_base_sha"] != ctx["live_source_base_sha"]:
            return "REBASE_REQUIRED"
        if target["work_branch"] != ctx["authorized_work_branch"]:
            return "INVALID_SCOPE"
        if not all(path_error(p, ctx) is None for p in target["changed_paths"]):
            return "INVALID_SCOPE"
    elif target["expected_revision"] != ctx["live_drive_revision"]:
        return "DOCUMENT_CONFLICT"
    if receipt["status"] != "VERIFIED":
        return "DOCUMENT_CONFLICT" if receipt["status"] == "CONFLICT" else "RETRYABLE_BLOCKED"
    if receipt["provider"] != target["provider"] or receipt["publication_id"] != intent["publication_id"] or receipt["idempotency_key"] != intent["idempotency_key"]:
        return "INVALID_SCOPE"
    if receipt["observed"] != ctx["trusted_provider_readback"]:
        return "SOURCE_SHA_MISMATCH"
    got = receipt["observed"]
    if target["provider"] == "GITHUB":
        if (got["repo_id"] != target["repo_id"] or got["work_branch"] != target["work_branch"]
                or got["tree_sha256"] != target["proposed_tree_sha256"]
                or got["artifact_sha256"] != intent["artifact_sha256"]):
            return "SOURCE_SHA_MISMATCH"
    elif got["file_id"] != target["file_id"] or got["exported_sha256"] != target["exported_sha256"]:
        return "DOCUMENT_CONFLICT"
    if not ctx["publication_enabled"]:
        return "CUTOVER_NOT_APPROVED"
    return "ALLOW"
