"""WG-D integration boundaries. Implementations belong to C/A/G/E/F/I as applicable.

D does not instantiate APSW, infer authenticated principals, write F reservations,
create browser chats, or publish GitHub relays. The C transaction adapter must
make the entire callback (including F's budget port work) atomic and enforce
scoped SQL/table mutation ownership, SQLite state machine, and idempotency.
"""
from __future__ import annotations
from typing import Any, Callable, Mapping, Protocol, TypeVar

T = TypeVar('T')


class ScopedTx(Protocol):
    def one(self, sql: str, params: tuple[Any, ...] = ()) -> Mapping[str, Any] | None: ...
    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[Mapping[str, Any]]: ...
    def change(self, sql: str, params: tuple[Any, ...] = ()) -> int: ...


class ScopedStorage(Protocol):
    def read(self, project_id: str, fn: Callable[[ScopedTx], T]) -> T: ...
    def command(self, project_id: str, operation: str, request_id: str,
                payload_sha256: str, fn: Callable[[ScopedTx], T]) -> T: ...


class Authorization(Protocol):
    def require(self, actor: Mapping[str, Any], project_id: str,
                operation: str, subject_id: str = '') -> None: ...


class VerifiedBriefs(Protocol):
    def get_verified(self, *, project_id: str, content_sha256: str) -> bytes: ...


class AtomicResourceReservations(Protocol):
    """F-owned writes, executed in the same C-scoped transaction as a lease."""
    def reserve(self, tx: ScopedTx, *, project_id: str, operation_id: str,
                resource_amounts: tuple[tuple[str, int], ...]) -> None: ...
    def release(self, tx: ScopedTx, *, project_id: str, operation_id: str) -> None: ...


class IndependentAcceptance(Protocol):
    """E verifies immutable, source/acceptance-exact independent QA evidence."""
    def require_accepted(self, tx: ScopedTx, *, project_id: str, task_id: str,
                         lease_generation: int, evidence_digest: str) -> None: ...
    def require_failed(self, tx: ScopedTx, *, project_id: str, task_id: str,
                       evidence_digest: str) -> None: ...


class AdvisoryStopPort(Protocol):
    """I requests a GitHub STOP for an enrolled session; no browser kill claim."""
    def notify_task_canceled(self, *, project_id: str, task_id: str,
                             lease_generation: int, reason: str) -> None: ...


class VerifiedTaskProgress(Protocol):
    """I/G/E enforce a verified receipt for the actual lease and transition."""
    def require_stage(self, tx: ScopedTx, *, project_id: str, task_id: str,
                      lease_generation: int, next_state: str,
                      evidence_digest: str) -> None: ...
