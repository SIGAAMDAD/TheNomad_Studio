"""Provider read-only port and exact metadata observations (no source bytes)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, Sequence, FrozenSet


@dataclass(frozen=True)
class RefObservation:
    repository: str
    ref: str
    commit_sha: str


@dataclass(frozen=True)
class CommitObservation:
    repository: str
    commit_sha: str
    tree_sha: str


@dataclass(frozen=True)
class TreeEntry:
    path: str
    kind: str  # blob / tree / symlink / submodule
    object_sha: str
    mode: str


@dataclass(frozen=True)
class TreeObservation:
    repository: str
    tree_sha: str
    entries: tuple[TreeEntry, ...]
    complete: bool


@dataclass(frozen=True)
class CIObservation:
    repository: str
    run_id: str
    head_sha: str
    workflow: str
    status: str
    conclusion: str | None


class ProviderUnavailable(OSError):
    """Provider authentication, reachability, throttling or readback failure."""


class SourceReader(Protocol):
    # Operationally supplied by a separate, restricted verifier credential.
    credential_scopes: FrozenSet[str]
    independent_readback: bool

    def get_ref(self, repository: str, ref: str) -> RefObservation: ...
    def get_commit(self, repository: str, sha: str) -> CommitObservation: ...
    def get_tree(self, repository: str, tree_sha: str, *, max_entries: int) -> TreeObservation: ...
    def get_ci(self, repository: str, run_id: str) -> CIObservation: ...
