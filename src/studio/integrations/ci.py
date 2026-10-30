"""GitHub Actions adapter: exact source SHA, allowlisted workflow target, confirmed run evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote
from .common import IntegrationError, ProjectBinding, ProviderFailure, RemoteUnknown, git_sha, identifier
from .github import GitHub


@dataclass(frozen=True)
class BuildTarget:
    target_id: str
    workflow_id: str
    ref: str

    def __post_init__(self):
        identifier(self.target_id)
        if not self.workflow_id.endswith(".yml") and not self.workflow_id.endswith(".yaml"):
            raise IntegrationError("invalid", "workflow must name pre-existing Actions YAML")
        if "/" in self.workflow_id or ".." in self.workflow_id:
            raise IntegrationError("invalid", "unsafe workflow path")


@dataclass(frozen=True)
class BuildObservation:
    run_id: int
    head_sha: str
    status: str
    conclusion: str | None
    url: str


class GitHubActions:
    """Configured workflow must expose intent ID in run-name, bind supplied head SHA in CI.

    A successful dispatch 204 only means request accepted. No run ID or PASS is inferred.
    """
    def __init__(self, github: GitHub, binding: ProjectBinding, targets: dict[str, BuildTarget]):
        binding.require(github.channel.project_id)
        self.github = github
        self.binding = binding
        self.targets = targets

    def dispatch(self, *, project_id: str, target_id: str, source_sha: str, intent_id: str,
                 effect_intent_recorded: bool) -> None:
        self.binding.require(project_id)
        if not effect_intent_recorded:
            raise IntegrationError("forbidden", "build effect intent must be recorded before dispatch")
        identifier(intent_id)
        source_sha = git_sha(source_sha)
        target = self.targets.get(identifier(target_id))
        if target is None or target.ref != self.github.channel.ref:
            raise IntegrationError("capability_unavailable", "unconfigured build target/ref")
        # The workflow_dispatch ref must still point at the exact intended source.
        if self.github.branch_tip() != source_sha:
            raise IntegrationError("stale_revision", "build source revision differs from configured branch tip")
        path = f"/actions/workflows/{quote(target.workflow_id, safe='')}/dispatches"
        self.github._request("POST", path,
                             {"ref": target.ref, "inputs": {"nomad_intent_id": intent_id,
                                                               "nomad_expected_sha": source_sha}}, expect=(204,))
        # Caller must reconcile actual workflow run, not claim dispatch == CI executed.

    def observe(self, *, project_id: str, target_id: str, source_sha: str, intent_id: str) -> BuildObservation | None:
        self.binding.require(project_id)
        identifier(intent_id)
        source_sha = git_sha(source_sha)
        target = self.targets.get(identifier(target_id))
        if target is None:
            raise IntegrationError("capability_unavailable", "unknown build target")
        # Multiple pages; fail closed if pagination cannot be exhausted in bounded search.
        matches = []
        for page in range(1, 11):
            path = (f"/actions/workflows/{quote(target.workflow_id, safe='')}/runs?event=workflow_dispatch"
                    f"&branch={quote(target.ref, safe='')}&per_page=100&page={page}")
            result = self.github._request("GET", path)
            if not isinstance(result, dict) or not isinstance(result.get("workflow_runs"), list):
                raise ProviderFailure("malformed CI listing")
            runs = result["workflow_runs"]
            for run in runs:
                if not isinstance(run, dict):
                    raise ProviderFailure("malformed CI run")
                # Only a uniquely correlated run name is accepted; a GitHub actor cannot declare approval.
                if (run.get("display_title") == f"nomad:{intent_id}" and run.get("head_sha") == source_sha and
                    run.get("path", "").split("@")[0] == f".github/workflows/{target.workflow_id}"):
                    if type(run.get("id")) is not int or run["id"] < 1:
                        raise ProviderFailure("malformed Actions run ID")
                    matches.append(BuildObservation(run["id"], source_sha, run.get("status", "unknown"),
                                                    run.get("conclusion"), run.get("html_url", "")))
            if len(runs) < 100:
                break
        else:
            raise RemoteUnknown("CI run listing not exhausted; run remains UNKNOWN")
        if len(matches) > 1:
            raise RemoteUnknown("ambiguous workflow runs for exact intent")
        return matches[0] if matches else None

    def verify_run(self, *, project_id: str, target_id: str, intent_id: str, run_id: int, expected_sha: str) -> BuildObservation:
        self.binding.require(project_id)
        identifier(intent_id)
        target = self.targets.get(identifier(target_id))
        if target is None:
            raise IntegrationError("capability_unavailable", "unknown workflow target")
        if type(run_id) is not int or run_id < 1:
            raise IntegrationError("invalid", "invalid Actions run ID")
        obj = self.github._request("GET", "/actions/runs/" + str(run_id))
        if (not isinstance(obj, dict) or obj.get("head_sha") != git_sha(expected_sha) or obj.get("id") != run_id or
            obj.get("display_title") != f"nomad:{intent_id}" or
            obj.get("path", "").split("@")[0] != f".github/workflows/{target.workflow_id}"):
            raise IntegrationError("conflict", "Actions run not tied to pinned source/workflow/intent")
        if obj.get("status") not in ("queued", "in_progress", "completed", "pending", "waiting", "requested"):
            raise ProviderFailure("unrecognized Actions status")
        if obj.get("conclusion") is not None and obj.get("conclusion") not in ("success", "failure", "cancelled", "skipped", "timed_out", "action_required", "neutral", "stale", "startup_failure"):
            raise ProviderFailure("unrecognized Actions conclusion")
        return BuildObservation(run_id, expected_sha, obj["status"], obj.get("conclusion"), obj.get("html_url", ""))
