"""WG-M project domain commands via the WG-C physical-transaction boundary.

The injected project state port MUST implement the named operations atomically
in C's APSW writer. Browser-provided identity strings do not grant owner rights.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Mapping

from .errors import StudioError

SHA = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class Actor:
    principal_id: str
    issuer: str
    allowed_project_ids: frozenset[str]
    trusted_proof_ref: str
    owner_verified: bool = False

    def require_verified(self) -> None:
        if not self.principal_id or not self.issuer or not self.trusted_proof_ref:
            raise StudioError("forbidden", "verified operator identity required")

    def require_scope(self, project_id: str) -> None:
        self.require_verified()
        if project_id not in self.allowed_project_ids:
            raise StudioError("forbidden", "missing authenticated project grant")

    def require_owner(self) -> None:
        self.require_verified()
        if not self.owner_verified:
            raise StudioError("requires_owner", "owner-authenticated command required")


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    revision: int
    state: str


class ProjectTransactionPort(Protocol):
    def bootstrap_project(self, *, principal_id: str, request_id: str,
                          request_body_sha256: str, name: str,
                          charter_digest: str) -> Project: ...

    def get_project(self, *, project_id: str, principal_id: str) -> Project | None: ...

    def list_granted_projects(self, *, principal_id: str, limit: int) -> list[Project]: ...

    def configure_project(self, *, project_id: str, principal_id: str,
                          request_id: str, request_body_sha256: str,
                          config_digest: str, expected_revision: int) -> Project: ...


class CapabilityProbe(Protocol):
    def probe(self, *, project_id: str) -> Mapping[str, bool]:
        """Return actual per-project installed/authenticated/executable readiness."""
        ...


def require_sha(value: str) -> None:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise StudioError("invalid", "expected SHA-256 content digest")


def request_hash(*parts: object) -> str:
    from .intake import digest
    import json
    return digest(json.dumps(parts, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


class ProjectService:
    def __init__(self, store: ProjectTransactionPort, capabilities: CapabilityProbe):
        self.store, self.capabilities = store, capabilities

    def createProject(self, actor: Actor, request: dict) -> dict:
        actor.require_owner()
        name = request["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 255:
            raise StudioError("invalid", "invalid project name")
        charter = request["charter_digest"]
        require_sha(charter)
        project = self.store.bootstrap_project(
            principal_id=actor.principal_id, request_id=request["request_id"],
            request_body_sha256=request_hash(name, charter),
            name=name, charter_digest=charter)
        return {"project_id": project.project_id, "revision": project.revision}

    def listProjects(self, actor: Actor) -> dict:
        actor.require_verified()
        projects = self.store.list_granted_projects(principal_id=actor.principal_id, limit=100)
        return {"items": [{"project_id": x.project_id, "name": x.name,
                           "revision": x.revision, "state": x.state} for x in projects]}

    def getProject(self, actor: Actor, project_id: str) -> dict:
        actor.require_scope(project_id)
        record = self.store.get_project(project_id=project_id, principal_id=actor.principal_id)
        if record is None:
            raise StudioError("missing", "project unavailable")
        return {"project_id": record.project_id, "name": record.name,
                "revision": record.revision, "state": record.state}

    def configureProject(self, actor: Actor, project_id: str, request: dict) -> dict:
        actor.require_scope(project_id)
        require_sha(request["config_digest"])
        if not isinstance(request["expected_revision"], int) or isinstance(request["expected_revision"], bool) or request["expected_revision"] < 1:
            raise StudioError("invalid", "revision must be positive")
        updated = self.store.configure_project(
            project_id=project_id, principal_id=actor.principal_id,
            request_id=request["request_id"],
            request_body_sha256=request_hash(request["config_digest"], request["expected_revision"]),
            config_digest=request["config_digest"],
            expected_revision=request["expected_revision"])
        return {"project_id": updated.project_id, "revision": updated.revision}

    def preflightProject(self, actor: Actor, project_id: str) -> dict:
        actor.require_scope(project_id)
        observed = self.capabilities.probe(project_id=project_id)
        if not observed:
            return {"project_id": project_id, "ready": False,
                    "missing_capabilities": ["capability_probe_unavailable"]}
        missing = sorted(k for k, working in observed.items() if working is not True)
        return {"project_id": project_id, "ready": not missing,
                "missing_capabilities": missing}
