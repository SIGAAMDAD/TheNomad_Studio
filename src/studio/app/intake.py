"""Validate a worker's changed-files-only handoff before integration.

Metadata is out-of-band and untrusted until verified. Checks do not claim GitHub
identity, independent QA, or delivery. All entries must belong to one worker.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

from .errors import StudioError

HEX256 = re.compile(r"^[a-f0-9]{64}$")
GROUPS = tuple("ABCDEFGHIJKLM")
MAX_FILES = 10_000
MAX_TOTAL_SIZE = 512 * 1024 * 1024
MAX_FILE_SIZE = 128 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized(path: str) -> str:
    if (not isinstance(path, str) or not path or "\\" in path
            or path.startswith("/") or "\x00" in path):
        raise StudioError("invalid", "unsafe contribution path")
    pure = PurePosixPath(path)
    if any(x in (".", "..", "") for x in path.split("/")) or pure.is_absolute():
        raise StudioError("invalid", "unsafe contribution path")
    if str(pure) != path or ":" in path.split("/")[0]:
        raise StudioError("invalid", "noncanonical contribution path")
    return path


def is_owned(path: str, patterns: tuple[str, ...]) -> bool:
    path = normalized(path)
    return any(path.startswith(rule[:-3] + "/") if rule.endswith("/**")
               else path == rule for rule in patterns)


def _hash_ok(value: Any) -> bool:
    return isinstance(value, str) and HEX256.fullmatch(value) is not None


@dataclass(frozen=True)
class Change:
    path: str
    prior_sha256: str | None
    new_sha256: str | None
    content: bytes | None


@dataclass(frozen=True)
class Handoff:
    group: str
    plan_sha256: str
    base_revision_id: str
    changes: tuple[Change, ...]
    test_evidence: tuple[dict[str, Any], ...]


class Roster:
    def __init__(self, plan_json: bytes, workgroups_json: bytes):
        self.plan_sha256 = digest(plan_json)
        plan = json.loads(plan_json)
        registry = json.loads(workgroups_json)
        if plan.get("constraints", {}).get("expected_workgroups") != 13 or registry.get("workers") != 13:
            raise StudioError("invalid", "wrong number of workgroups")
        if (plan.get("constraints", {}).get("expected_waves") != 1 or
                registry.get("authoring_waves") != 1 or
                registry.get("extra_workgroup_git_branches") != 0 or
                registry.get("integration_tree_count") != 1):
            raise StudioError("invalid", "unexpected wave, branch or integration tree")
        groups = registry["groups"]
        if {g["id"] for g in groups} != set(GROUPS) or len(groups) != 13:
            raise StudioError("invalid", "worker registry must contain A-M exactly once")
        self.paths: dict[str, tuple[str, ...]] = {
            g["id"]: tuple(g["source_paths"]) for g in groups
        }
        # Overlap check: boundary-safe and exact names, not naive startswith.
        roots: dict[str, str] = {}
        for owner, patterns in self.paths.items():
            for pattern in patterns:
                root = pattern[:-3] if pattern.endswith("/**") else pattern
                for other, previous in roots.items():
                    if root == other or root.startswith(other + "/") or other.startswith(root + "/"):
                        raise StudioError("invalid", f"source path overlap: {owner} with {previous}")
                roots[root] = owner

    @classmethod
    def load(cls, plan_root: Path) -> "Roster":
        return cls((plan_root / "plan.json").read_bytes(),
                   (plan_root / "WORKERS/workgroups.json").read_bytes())

    def verify_handoff(self, zip_path: Path, metadata: dict[str, Any]) -> Handoff:
        group = metadata.get("group")
        if group not in self.paths or metadata.get("plan_sha256") != self.plan_sha256:
            raise StudioError("invalid", "worker identity or frozen plan digest mismatch")
        base = metadata.get("base_revision_id")
        if not isinstance(base, str) or not base or len(base) > 200:
            raise StudioError("invalid", "missing source baseline revision")
        manifest = metadata.get("files")
        if not isinstance(manifest, dict) or len(manifest) > MAX_FILES:
            raise StudioError("invalid", "file manifest missing or too large")
        parsed: dict[str, Change] = {}
        folded: set[str] = set()
        for path, spec in manifest.items():
            normalized(path)
            if path.casefold() in folded:
                raise StudioError("invalid", "case-insensitive file collision")
            folded.add(path.casefold())
            if not is_owned(path, self.paths[group]):
                raise StudioError("forbidden", f"worker {group} cannot change {path}")
            if not isinstance(spec, dict) or set(spec) != {"before", "after"}:
                raise StudioError("invalid", "expected before/after digests for each path")
            old, new = spec["before"], spec["after"]
            if old is not None and not _hash_ok(old) or new is not None and not _hash_ok(new):
                raise StudioError("invalid", "invalid change digest")
            if old is None and new is None or old == new:
                raise StudioError("invalid", "manifest describes no change")
            parsed[path] = Change(path, old, new, None)
        contents: dict[str, bytes] = {}
        total = 0
        with ZipFile(zip_path) as archive:
            entries = [i for i in archive.infolist() if not i.is_dir()]
            if len(entries) > MAX_FILES:
                raise StudioError("invalid", "too many files")
            for info in entries:
                path = normalized(info.filename)
                if path in contents or path.casefold() not in folded:
                    raise StudioError("invalid", "unlisted or duplicate file")
                if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                    raise StudioError("invalid", "symlink is not an allowed handoff entry")
                if info.file_size > MAX_FILE_SIZE:
                    raise StudioError("invalid", "file exceeds size limit")
                total += info.file_size
                if total > MAX_TOTAL_SIZE:
                    raise StudioError("invalid", "handoff exceeds size limit")
                with archive.open(info) as stream:
                    data = stream.read(info.file_size + 1)
                if len(data) != info.file_size:
                    raise StudioError("invalid", "uncompressed size inconsistent")
                if digest(data) != parsed[path].new_sha256:
                    raise StudioError("invalid", f"sha256 mismatch: {path}")
                contents[path] = data
        expected_contents = {p for p, change in parsed.items() if change.new_sha256 is not None}
        if set(contents) != expected_contents:
            raise StudioError("invalid", "missing or extra ZIP paths")
        evidence = metadata.get("tests", [])
        if not isinstance(evidence, list) or any(not isinstance(x, dict) for x in evidence):
            raise StudioError("invalid", "tests must be submitted as records")
        return Handoff(group, self.plan_sha256, base,
                       tuple(Change(c.path, c.prior_sha256, c.new_sha256, contents.get(c.path))
                             for c in parsed.values()), tuple(evidence))
