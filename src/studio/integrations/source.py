"""GitHub source revision/readback from a configured existing source repository/ref.

Source identity is git commit SHA; branch labels, commit messages and authorship
never carry owner/QA authority. This does not create or push workgroup branches.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from .common import IntegrationError, ProjectBinding, ProviderFailure, RemoteUnknown, git_sha, sha256
from .http import HttpPort, JsonProvider


@dataclass(frozen=True)
class SourceSnapshot:
    project_id: str
    repository: str
    ref: str
    commit_sha: str
    path: str
    body_sha256: str
    body: bytes


class GitHubSource:
    def __init__(self, http: HttpPort, token_supplier: Callable[[], str], binding: ProjectBinding,
                 owner: str, repo: str, existing_ref: str):
        from .github import GitHubChannel
        # validates owner/repo/ref; no branch creation operation implemented
        validated = GitHubChannel(binding.project_id, owner, repo, existing_ref)
        self.binding = binding
        self.owner, self.repo, self.ref = validated.owner, validated.repo, validated.ref
        self.prefix = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        self.api = JsonProvider(http, token_supplier, "https://api.github.com")

    def _request(self, method: str, path: str):
        return self.api.request(method, self.prefix + path)

    def ref_sha(self, *, project_id: str) -> str:
        self.binding.require(project_id)
        doc = self._request("GET", "/git/ref/heads/" + quote(self.ref, safe="/"))
        if not isinstance(doc, dict) or not isinstance(doc.get("object"), dict):
            raise ProviderFailure("malformed source ref response")
        return git_sha(doc["object"]["sha"])

    def confirm_reachable(self, *, project_id: str, commit_sha: str) -> str:
        self.binding.require(project_id)
        commit_sha = git_sha(commit_sha)
        head = self.ref_sha(project_id=project_id)
        if commit_sha != head:
            relation = self._request("GET", f"/compare/{commit_sha}...{head}")
            if not isinstance(relation, dict) or relation.get("status") != "ahead":
                raise IntegrationError("stale_revision", "source SHA not on configured ref ancestry")
        return head

    def read_file(self, *, project_id: str, commit_sha: str, path: str, max_bytes: int = 50_000_000) -> SourceSnapshot:
        self.binding.require(project_id)
        git_sha(commit_sha)
        if not isinstance(path, str) or not path or path.startswith("/") or '\\' in path or any(part in ("", ".", "..") for part in path.split("/")):
            raise IntegrationError("invalid", "invalid source repository file path")
        self.confirm_reachable(project_id=project_id, commit_sha=commit_sha)
        obj = self._request("GET", "/contents/" + quote(path, safe="/") + "?ref=" + commit_sha)
        if not isinstance(obj, dict) or obj.get("type") != "file":
            raise ProviderFailure("malformed GitHub source response")
        # GitHub Contents API may omit bytes for files over 1 MiB. Fetch by the
        # blob SHA from the same pinned commit's path response, then verify SHA.
        source_obj = obj
        if obj.get("encoding") == "none":
            source_obj = self._request("GET", "/git/blobs/" + git_sha(obj["sha"]))
        if not isinstance(source_obj, dict) or source_obj.get("encoding") != "base64":
            raise ProviderFailure("source blob encoding not available")
        try:
            body = base64.b64decode(source_obj["content"], validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise ProviderFailure("source content encoding invalid") from exc
        if len(body) > max_bytes:
            raise IntegrationError("invalid", "source file too large for readback")
        git_blob = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
        if obj.get("sha") != git_blob:
            raise RemoteUnknown("GitHub source content hash did not match blob SHA")
        return SourceSnapshot(project_id, f"{self.owner}/{self.repo}", self.ref, commit_sha, path, sha256(body), body)
