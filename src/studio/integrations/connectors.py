"""Per-project credential-ref-only connector attachment; never return bearer secrets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol
from .common import IntegrationError, ProjectBinding, ProviderFailure, identifier
from .http import HttpPort, JsonProvider


class ConnectorRepository(Protocol):
    """C-owned per-project repository; must enforce idempotency and owner grants."""
    def record_connector(self, project_id: str, provider_key: str, credential_ref: str, account_id: str, request_id: str) -> None: ...


@dataclass(frozen=True)
class ConnectorAttachment:
    account_id: str
    state: str


class ConnectorService:
    def __init__(self, http: HttpPort, binding: ProjectBinding,
                 credential_resolver: Callable[[str], str], repository: ConnectorRepository):
        self.http = http
        self.binding = binding
        self.credential_resolver = credential_resolver
        self.repository = repository

    def attach(self, *, project_id: str, request_id: str, provider_key: str, credential_ref: str) -> ConnectorAttachment:
        self.binding.require(project_id)
        identifier(request_id)
        identifier(credential_ref)
        if provider_key not in ("github", "google_drive"):
            raise IntegrationError("capability_unavailable", "unsupported connector provider")
        token = self.credential_resolver(credential_ref)
        if not token:
            raise IntegrationError("capability_unavailable", "connector credential unresolved")
        if provider_key == "github":
            provider = JsonProvider(self.http, lambda: token, "https://api.github.com")
            info = provider.request("GET", "/user")
            if not isinstance(info, dict) or not isinstance(info.get("login"), str):
                raise ProviderFailure("cannot verify authenticated GitHub account")
            account_id = info["login"]
        else:
            provider = JsonProvider(self.http, lambda: token, "https://www.googleapis.com")
            info = provider.request("GET", "/drive/v3/about?fields=user(emailAddress,permissionId)")
            user = info.get("user") if isinstance(info, dict) else None
            if not isinstance(user, dict) or not isinstance(user.get("permissionId"), str):
                raise ProviderFailure("cannot verify authenticated Drive account")
            account_id = user["permissionId"]
        # Account proven by live identity endpoint; caller verifies auth/owner policy.
        self.repository.record_connector(project_id, provider_key, credential_ref, account_id, request_id)
        return ConnectorAttachment(account_id, "attached")
