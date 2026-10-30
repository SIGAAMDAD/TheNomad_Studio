"""WG-B service boundaries. WG-C supplies the APSW-backed transactional store.

Only the listed organization tables may be accessed through OrganizationTransaction.
The implementer must enforce table ownership, exact project authorization, unique
request-id/body digests, atomic commit/rollback, and replay of committed responses.
"""
from __future__ import annotations
from typing import Any, Callable, Mapping, Protocol, TypeVar

T = TypeVar('T')
OWNED_TABLES = frozenset({
    'departments', 'specialist_roles', 'cabals', 'cabal_members', 'deliberations',
    'package_revisions', 'package_dependencies', 'project_packages',
    'tool_entrypoints', 'tool_grants',
})

class OrganizationTransaction(Protocol):
    def one(self, table: str, **keys: str) -> dict[str, Any] | None: ...
    def many(self, table: str, **keys: str) -> list[dict[str, Any]]: ...
    def insert(self, table: str, record: Mapping[str, Any]) -> None: ...
    def change(self, table: str, keys: Mapping[str, str], values: Mapping[str, Any]) -> None: ...

class OrganizationStore(Protocol):
    def read(self, project_id: str, action: Callable[[OrganizationTransaction], T]) -> T: ...
    def mutate(self, *, project_id: str, principal_id: str, action_name: str,
               request_id: str, payload_digest: str,
               action: Callable[[OrganizationTransaction], T]) -> T: ...

class PermissionPolicy(Protocol):
    """Provided by authenticated WG-A/J policy; never based on relay role claims."""
    def check_membership(self, principal_id: str, project_id: str, member_id: str,
                         role_id: str, permission_digest: str) -> None: ...
    def check_tool_grants(self, principal_id: str, project_id: str,
                          grants: list[dict[str, str]]) -> None: ...

class TrustVerifier(Protocol):
    """Verify installed package provenance using configured trusted anchors."""
    def verify_package(self, *, manifest: bytes, signature_ref: str) -> None: ...

class ContentSource(Protocol):
    """Content-addressed bytes; absent bytes return None, never placeholder data."""
    def get(self, sha256_digest: str) -> bytes | None: ...

class HostReadiness(Protocol):
    """WG-H results: explicit verified test receipts for tool/smoke checks."""
    def tool_available(self, tool: str) -> bool: ...
    def validator_passed(self, validator_digest: str) -> bool: ...
