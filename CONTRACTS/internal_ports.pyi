from typing import Protocol, TypedDict, Mapping, Callable, Any

class GitHubRelayObservation(TypedDict):
    project_id: str
    channel_id: str
    remote_path: str
    commit_sha: str
    body_sha256: str
    verified: bool

class WorkShiftPacket(TypedDict):
    project_id: str
    worker_id: str
    shift_id: str
    role_manifest_digest: str
    canon_snapshot_id: str
    contract_digest: str
    github_repo: str
    github_ref: str
    inbox_path: str

class RelayTransport(Protocol):
    def publish_new_file(self, *, project_id: str, channel_id: str, path: str, content_digest: str, request_id: str) -> GitHubRelayObservation: ...
    def observe_exact(self, *, project_id: str, channel_id: str, path: str) -> GitHubRelayObservation | None: ...
    def scan_since(self, *, project_id: str, channel_id: str, verified_ref_sha: str | None, limit: int) -> list[GitHubRelayObservation]: ...

class WorkShiftCoordinator(Protocol):
    def register_worker(self, *, project_id: str, worker_id: str, browser_chat_ref: str, role_id: str, request_id: str) -> dict: ...
    def begin_shift(self, *, project_id: str, worker_id: str, shift_id: str, request_id: str) -> dict: ...
    def observe_hello(self, *, project_id: str, worker_id: str, shift_id: str, receipt_commit_sha: str, receipt_digest: str) -> dict: ...
    def request_stop(self, *, project_id: str, worker_id: str, shift_id: str, reason: str, request_id: str) -> dict: ...
    def observe_command_receipt(self, *, project_id: str, command_id: str, receipt_commit_sha: str, result_digest: str) -> dict: ...
