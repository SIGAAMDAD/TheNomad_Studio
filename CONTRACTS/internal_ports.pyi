from typing import Protocol, TypedDict, Literal
class GitHubRelayObservation(TypedDict):
    project_id: str
    channel_id: str
    remote_path: str
    commit_sha: str
    body_sha256: str
    verified: bool
class ManualLaunchPacket(TypedDict):
    session_id: str
    project_id: str
    task_id: str
    lease_generation: int
    role_manifest_digest: str
    snapshot_id: str
    context_digest: str
    github_repo: str
    github_ref: str
    inbox_path: str
    prompt_digest: str
class RelayTransport(Protocol):
    def publish_new_file(self, *, project_id: str, channel_id: str, path: str, content_digest: str, request_id: str) -> GitHubRelayObservation: ...
    def observe_exact(self, *, project_id: str, channel_id: str, path: str) -> GitHubRelayObservation | None: ...
    def scan_since(self, *, project_id: str, channel_id: str, verified_ref_sha: str | None, limit: int) -> list[GitHubRelayObservation]: ...
class ManualSessionCoordinator(Protocol):
    def prepare_launch(self, *, project_id: str, task_id: str, lease_generation: int, package_digest: str, request_id: str) -> ManualLaunchPacket: ...
    def prepare_resume(self, *, project_id: str, session_id: str, checkpoint_id: str, request_id: str) -> ManualLaunchPacket: ...
    def observe_github_receipt(self, *, project_id: str, channel_id: str, observation: GitHubRelayObservation) -> None: ...
    def request_stop(self, *, project_id: str, session_id: str, reason: str, request_id: str) -> str: ...
