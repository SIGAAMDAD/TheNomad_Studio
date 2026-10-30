"""WG-A integration boundary; no guessed WG-C, WG-G or WG-J implementation."""
from __future__ import annotations
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any, Protocol
from .model import Principal, ProjectState


class TrustedIdentity(Protocol):
    def authenticate(self, actor: dict[str, Any]) -> Principal:
        """J must verify a server-bound credential; never trust actor fields."""
        ...


class VerifiedBlobSource(Protocol):
    def read_verified(self, project_id: str, digest: str, purpose: str) -> bytes:
        """G/C must verify project scope, physical readback, SHA256 and durability."""
        ...


class DomainTransaction(Protocol):
    def __enter__(self) -> ProjectState: ...
    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool | None: ...


class GovernanceTransactions(Protocol):
    def project(self, project_id: str) -> DomainTransaction:
        """C implements atomic project-scoped read/write and conflict fencing."""
        ...


class SubjectCatalog(Protocol):
    def exists(self, project_id: str, subject_type: str, subject_id: str) -> bool:
        """Cross-domain subjects require actual D/B/G/M state, no invented IDs."""
        ...


class ImpactCatalog(Protocol):
    def referenced_tasks(self, project_id: str, clause_keys: set[str]) -> set[str]:
        """D's pinned dependencies, if integrated; no task mutation."""
        ...
