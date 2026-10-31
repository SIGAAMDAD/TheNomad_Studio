"""GitHub source/relay transport with immutable path publication and SHA readback.

The GitHub commit proves remote bytes, not which browser chat authored them.
No branch is created. The caller must durably record an effect intent BEFORE publish().
"""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from .common import IntegrationError, ProviderFailure, RemoteUnknown, RemotePreflightUnavailable, ProjectBinding, identifier, git_sha, sha256, parse_canonical
from .http import HttpPort, JsonProvider
from .relay import validate_relay, validate_receipt, validate_shift_receipt

_PART = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,240}\Z")


@dataclass(frozen=True)
class GitHubChannel:
    project_id: str
    owner: str
    repo: str
    ref: str  # pre-existing branch (Contents API requires a branch)
    root: str = ""  # defaults to .nomad/relays/<project_id>

    def __post_init__(self):
        identifier(self.project_id)
        if not all(_PART.fullmatch(x) and x not in (".", "..") for x in (self.owner, self.repo)):
            raise IntegrationError("invalid", "invalid GitHub repository")
        if not _REF.fullmatch(self.ref) or any(x in (".", "..", "") for x in self.ref.split("/")) or self.ref.endswith(".lock"):
            raise IntegrationError("invalid", "invalid pre-existing git ref")
        root = self.root or f".nomad/relays/{self.project_id}"
        if root != f".nomad/relays/{self.project_id}":
            raise IntegrationError("invalid", "relay path must be bound to project root")
        object.__setattr__(self, "root", root)

    def path(self, kind: str, recipient: str, name: str, *, message_id: str | None = None) -> str:
        identifier(recipient)
        identifier(name)
        if kind == "mail":
            return f"{self.root}/mail/{recipient}/{name}.json"
        if kind == "receipts":
            identifier(message_id or "")
            return f"{self.root}/receipts/{recipient}/{message_id}/{name}.json"
        if kind in ("handoffs", "reports"):
            return f"{self.root}/{kind}/{recipient}/{name}.json"
        raise IntegrationError("invalid", "unknown relay path type")


@dataclass(frozen=True)
class RemoteFile:
    project_id: str
    path: str
    commit_sha: str
    body_sha256: str
    body: bytes

    @property
    def verified(self) -> bool:
        return True


@dataclass(frozen=True)
class ScanPage:
    entries: tuple[RemoteFile, ...]
    head_sha: str
    next_offset: int | None
    complete_cursor: str | None


class GitHub:
    def __init__(self, http: HttpPort, token_supplier: Callable[[], str], channel: GitHubChannel, binding: ProjectBinding):
        binding.require(channel.project_id)
        self.channel = channel
        self.binding = binding
        self.api = JsonProvider(http, token_supplier, "https://api.github.com")
        self.prefix = f"/repos/{quote(channel.owner, safe='')}/{quote(channel.repo, safe='')}"

    def _request(self, method: str, path: str, data: dict | None = None, expect: tuple[int, ...] = (200,)):
        return self.api.request(method, self.prefix + path, data, expect=expect)

    def branch_tip(self) -> str:
        ref = quote(self.channel.ref, safe="")
        value = self._request("GET", f"/git/ref/heads/{ref}")
        if not isinstance(value, dict) or not isinstance(value.get("object"), dict):
            raise ProviderFailure("GitHub ref response malformed")
        return git_sha(value["object"]["sha"])

    def _tree(self, commit_sha: str) -> dict[str, str]:
        commit = self._request("GET", "/git/commits/" + git_sha(commit_sha))
        if not isinstance(commit, dict) or not isinstance(commit.get("tree"), dict):
            raise ProviderFailure("GitHub commit response malformed")
        tree = self._request("GET", "/git/trees/" + git_sha(commit["tree"]["sha"]) + "?recursive=1")
        if not isinstance(tree, dict) or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
            raise RemoteUnknown("GitHub tree listing incomplete; cursor unchanged")
        paths: dict[str, str] = {}
        for item in tree["tree"]:
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            path = item.get("path")
            blob_sha = item.get("sha")
            if not isinstance(path, str) or not isinstance(blob_sha, str):
                raise ProviderFailure("invalid GitHub tree entry")
            if path.startswith(self.channel.root + "/"):
                paths[path] = git_sha(blob_sha)
        return paths

    def _is_descendant(self, before: str, after: str) -> None:
        if before == after:
            return
        cmp = self._request("GET", f"/compare/{git_sha(before)}...{git_sha(after)}")
        if not isinstance(cmp, dict) or cmp.get("status") != "ahead":
            raise RemoteUnknown("relay history lost ancestry / branch rewritten")

    def _read_at(self, path: str, sha: str, tree_entry_sha: str | None = None) -> RemoteFile | None:
        if not path.startswith(self.channel.root + "/"):
            raise IntegrationError("forbidden", "remote path outside project root")
        safe_path = quote(path, safe="/")
        try:
            obj = self._request("GET", f"/contents/{safe_path}?ref={quote(git_sha(sha), safe='')}")
        except ProviderFailure as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(obj, dict) or obj.get("type") != "file" or obj.get("encoding") != "base64":
            raise ProviderFailure("unexpected GitHub file response")
        try:
            body = base64.b64decode(obj["content"], validate=False)
        except (KeyError, ValueError, TypeError) as exc:
            raise ProviderFailure("malformed GitHub content") from exc
        expected_git = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
        if obj.get("sha") != expected_git or (tree_entry_sha is not None and tree_entry_sha != expected_git):
            raise RemoteUnknown("GitHub remote body/blob hash mismatch")
        return RemoteFile(self.channel.project_id, path, git_sha(sha), sha256(body), body)

    def observe_exact(self, path: str, *, pinned_commit: str | None = None) -> RemoteFile | None:
        commit = git_sha(pinned_commit) if pinned_commit else self.branch_tip()
        # A commit-pinned contents GET validates Git's blob SHA in _read_at.
        # A recursive repository tree scan per mailbox is unnecessary here,
        # and can fail solely because the repository tree was truncated.
        return self._read_at(path, commit)

    def publish(self, path: str, body: bytes, *, expected_digest: str, effect_intent_recorded: bool) -> RemoteFile:
        """Caller must persist an exact project/path/hash intent first; no optimistic success."""
        if not effect_intent_recorded:
            raise IntegrationError("forbidden", "durable effect intent required")
        if sha256(body) != expected_digest:
            raise IntegrationError("invalid", "publication payload hash mismatch")
        self._validate_path(path, body)
        try:
            existing = self.observe_exact(path)
        except (RemoteUnknown, ProviderFailure) as exc:
            # A pre-publication outage cannot mean the immutable PUT happened.
            raise RemotePreflightUnavailable(
                "GitHub pre-publication readback unavailable",
                status=getattr(exc, "status", None),
                retry_after=getattr(exc, "retry_after", None)) from exc
        if existing:
            if existing.body_sha256 != expected_digest:
                raise IntegrationError("conflict", "immutable remote path already has different bytes")
            return existing
        # PUT without SHA is create-only in the GitHub Contents API; no overwrite.
        data = {"message": "Nomad immutable relay publication", "content": base64.b64encode(body).decode("ascii"), "branch": self.channel.ref}
        try:
            response = self._request("PUT", "/contents/" + quote(path, safe="/"), data, expect=(201,))
        except (RemoteUnknown, ProviderFailure) as exc:
            # On transport ambiguity/conflict the only safe next step is same-path readback.
            observed = self.observe_exact(path)
            if observed:
                if observed.body_sha256 != expected_digest:
                    raise IntegrationError("conflict", "remote path contains another payload") from exc
                return observed
            raise RemoteUnknown("publication not proven; reconcile same path before any retry") from exc
        if not isinstance(response, dict) or not isinstance(response.get("commit"), dict):
            raise RemoteUnknown("GitHub write returned no verified commit")
        commit = git_sha(response["commit"]["sha"])
        observed = self.observe_exact(path, pinned_commit=commit)
        if observed is None or observed.body_sha256 != expected_digest:
            raise RemoteUnknown("created file failed exact-commit remote verification")
        return observed

    def _validate_path(self, path: str, body: bytes) -> None:
        root = self.channel.root + "/"
        if not path.startswith(root) or len(body) > 1_048_576:
            raise IntegrationError("invalid", "invalid relay file path/size")
        rel = path[len(root):].split("/")
        if len(rel) == 3 and rel[0] == "mail":
            session, file_id = rel[1], rel[2]
            if not file_id.endswith(".json"):
                raise IntegrationError("invalid", "invalid mail suffix")
            # A durable assignment addresses the persistent conversation and
            # the currently observed shift; it is not a new browser session.
            raw = parse_canonical(body)
            if "worker_id" in raw:
                from studio.agents.shift_runtime import ShiftCommand, ShiftProtocolError
                try:
                    cmd = ShiftCommand.parse(raw)
                except ShiftProtocolError as exc:
                    raise IntegrationError("invalid", str(exc)) from exc
                if (cmd.project_id != self.channel.project_id or
                        cmd.worker_id != identifier(session) or
                        file_id != cmd.command_id + ".json"):
                    raise IntegrationError("forbidden", "worker shift command misaddressed")
            else:
                obj, _ = validate_relay(body, project_id=self.channel.project_id, recipient=identifier(session))
                if file_id != obj["message_id"] + ".json":
                    raise IntegrationError("invalid", "message/path ID mismatch")
        elif len(rel) == 4 and rel[0] == "receipts":
            if not rel[-1].endswith(".json"):
                raise IntegrationError("invalid", "invalid receipt suffix")
            parsed = parse_canonical(body)
            if 'shift_id' in parsed:
                obj, _ = validate_shift_receipt(body, project_id=self.channel.project_id,
                                                recipient=identifier(rel[1]))
                message_id = obj['command_id']
            else:
                obj, _ = validate_receipt(body, project_id=self.channel.project_id,
                                          recipient=identifier(rel[1]))
                message_id = obj['message_id']
            if rel[2] != message_id or rel[3] != obj["receipt_id"] + ".json":
                raise IntegrationError("invalid", "receipt/path ID mismatch")
        elif len(rel) == 3 and rel[0] in ("handoffs", "reports"):
            identifier(rel[1])
            if not rel[2].endswith(".json"):
                raise IntegrationError("invalid", "invalid auxiliary relay suffix")
            identifier(rel[2][:-5])
            doc = parse_canonical(body)
            if doc.get("project_id") != self.channel.project_id:
                raise IntegrationError("forbidden", "auxiliary relay wrong project")
        else:
            raise IntegrationError("invalid", "invalid immutable relay path")

    def scan_since(self, verified_ref_sha: str | None, *, limit: int, offset: int = 0, expected_head: str | None = None) -> ScanPage:
        """Exhaust pages before using complete_cursor; preserve cursor on any incomplete scan."""
        if limit < 1 or limit > 500 or offset < 0:
            raise IntegrationError("invalid", "invalid poll limit or offset")
        tip = self.branch_tip()
        if expected_head is not None and tip != git_sha(expected_head):
            raise RemoteUnknown("head changed during paginated read; rescan")
        old = {}
        if verified_ref_sha:
            self._is_descendant(git_sha(verified_ref_sha), tip)
            old = self._tree(verified_ref_sha)
        current = self._tree(tip)
        for path, blob in old.items():
            if current.get(path) != blob:
                raise RemoteUnknown("relay history deleted or overwritten an immutable file")
        added = sorted(set(current).difference(old))
        if offset > len(added):
            raise IntegrationError("invalid", "poll offset exceeds verified enumeration")
        # Never advance cursor when the directory contains unrecognized files.
        for path in added:
            if not (path.endswith(".json") and path.startswith(self.channel.root + "/")):
                raise RemoteUnknown("unrecognized new file under relay root")
        page = added[offset:offset + limit]
        entries = []
        for path in page:
            obj = self._read_at(path, tip, current[path])
            if obj is None:
                raise RemoteUnknown("file omitted during exact-commit readback")
            self._validate_path(path, obj.body)
            entries.append(obj)
        complete = offset + len(page) >= len(added)
        return ScanPage(tuple(entries), tip, None if complete else offset + len(page), tip if complete else None)
