"""WG-I business boundary contracts; adapters are implemented by WG-C/G/A/B/D/J."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class Channel:
    project_id: str
    channel_id: str
    github_repo: str
    github_ref: str
    root_path: str
    enabled: bool = True


@dataclass(frozen=True)
class CanonCitation:
    """A-verified and source-pinned fact/clause reference, not a chat assertion."""
    snapshot_id: str
    fact_id: str
    edition_id: str
    clause_id: str
    revision: int
    source_digest: str
    text: str


@dataclass(frozen=True)
class Assignment:
    project_id: str
    task_id: str
    lease_generation: int
    lease_status: str
    snapshot_id: str
    contract_digest: str
    package_digest: str
    role_manifest_digest: str
    context_digest: str
    source_baseline_sha: str
    actor_principal_id: str
    channel: Channel
    cabal_id: str
    role_id: str
    # Retrieval is a verified service output; model-provided claims do not bind authority.
    cited_context: tuple[CanonCitation, ...] = ()
    role_guidance: str = ""


@dataclass(frozen=True)
class Session:
    project_id: str
    session_id: str
    task_id: str
    lease_generation: int
    snapshot_id: str
    contract_digest: str
    package_digest: str
    author_principal_id: str
    channel_id: str
    state: str
    prompt_digest: str
    created_at: str
    last_observed_at: str | None = None
    parent_session_id: str | None = None


@dataclass(frozen=True)
class Checkpoint:
    project_id: str
    checkpoint_id: str
    session_id: str
    context_digest: str
    relay_cursor: str | None
    evidence_digest: str
    verified: bool = False  # C derives this from a matching verified GitHub receipt


@dataclass(frozen=True)
class RemoteFile:
    """Only trusted G adapter may produce verified=True after remote GitHub readback."""
    project_id: str
    channel_id: str
    path: str
    repo: str
    ref: str
    commit_sha: str
    body: bytes
    body_sha256: str
    verified: bool


@dataclass(frozen=True)
class RemotePage:
    files: tuple[RemoteFile, ...]
    observed_ref_sha: str | None
    complete: bool
    ancestry_verified: bool = True
    next_page_token: str | None = None


@dataclass(frozen=True)
class RelayMessage:
    project_id: str
    message_id: str
    channel_id: str
    from_session_id: str | None
    to_session_id: str
    task_id: str
    lease_generation: int
    canon_snapshot_id: str
    contract_digest: str
    kind: str
    envelope_digest: str
    payload_digest: str
    remote_path: str
    state: str
    remote_commit_sha: str | None = None


@dataclass(frozen=True)
class RelayReceipt:
    project_id: str
    receipt_id: str
    message_id: str
    channel_id: str
    recipient_session_id: str
    kind: str
    observed_message_sha256: str
    contract_digest: str
    remote_path: str
    remote_commit_sha: str
    payload_digest: str
    observed_at: str


class AuthorityPort(Protocol):
    """Authenticated J actor checked against A project grant and D lease/B role."""
    def require_project_action(self, actor: Mapping[str, Any], project_id: str, action: str) -> str: ...
    def resolve_assignment(self, project_id: str, task_id: str, generation: int, package_digest: str) -> Assignment: ...
    def resolve_resume(self, old: Session) -> Assignment: ...
    def allowed_relay(self, sender: Session, recipient: Session, kind: str) -> bool: ...
    def quiet_seconds(self, project_id: str) -> int | None: ...


class SessionStorePort(Protocol):
    """Business mutations are WG-I-owned, physically serialized by WG-C.

    create_session, stage_message, record_observation, record_receipt and
    checkpoint storage require atomic project-scoped transactions and consistent
    request idempotency in C; callers never write SQL directly.
    """
    def create_session(self, session: Session, request_id: str, request_hash: str) -> Session: ...
    def get_session(self, project_id: str, session_id: str) -> Session | None: ...
    def transition(self, project_id: str, session_id: str, expected: frozenset[str], new_state: str) -> Session: ...
    def list_sessions(self, project_id: str) -> list[Session]: ...
    def get_checkpoint(self, project_id: str, checkpoint_id: str) -> Checkpoint | None: ...
    def record_checkpoint(self, checkpoint: Checkpoint) -> None: ...
    def stage_message(self, message: RelayMessage, request_id: str, request_hash: str) -> RelayMessage: ...
    def get_message(self, project_id: str, message_id: str) -> RelayMessage | None: ...
    def record_observation(self, message: RelayMessage, file: RemoteFile) -> None: ...
    def record_receipt(self, receipt: RelayReceipt, file: RemoteFile) -> None: ...
    def get_receipts(self, project_id: str, message_id: str) -> list[RelayReceipt]: ...
    def list_unresolved(self, project_id: str, channel_id: str) -> list[RelayMessage]: ...
    def get_cursor(self, project_id: str, channel_id: str) -> str | None: ...
    def commit_cursor(self, project_id: str, channel_id: str, ref_sha: str) -> None: ...
    def record_lineage(self, child: Session, parent: Session) -> None: ...
    def record_effect_intent(self, project_id: str, message: RelayMessage, request_id: str) -> None: ...


class RelayGatewayPort(Protocol):
    """WG-G owns real publish, repo/ref ancestry, immutable paths and byte readback."""
    def stage_content(self, *, project_id: str, content: bytes) -> str: ...
    def publish_new_file(self, *, project_id: str, channel_id: str, path: str, content_digest: str, request_id: str) -> None: ...
    def readback_exact(self, *, project_id: str, channel_id: str, path: str) -> RemoteFile | None: ...
    def scan_since(self, *, project_id: str, channel_id: str, verified_ref_sha: str | None, limit: int) -> RemotePage: ...
    def scan_next(self, *, project_id: str, channel_id: str, page_token: str, limit: int) -> RemotePage: ...
