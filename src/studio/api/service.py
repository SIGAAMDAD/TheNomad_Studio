"""Generated-contract dispatch and authorization with mandatory storage idempotency.

Domain implementations are supplied by A-I/M; J does not own their tables or
business decisions. WG-C supplies the atomic transaction/idempotency runner.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable, Protocol

from .contracts import FrozenContracts, Operation
from .errors import ApiError
from .identity import IdentityVerifier, ProjectGrantsReader, authenticated_actor


class AtomicMutationRunner(Protocol):
    """WG-C-owned scoped APSW transaction and stable replay contract.

    Must atomically: reserve (project_id,owner,request_id) or bootstrap
    (principal_id,request_id), compare payload_digest, execute callback under
    scoped domain command guard, persist result and commit. For a finished
    identical replay return the original response, without running callback.
    For changed payload return conflict; ambiguous external effect is UNKNOWN,
    never re-dispatched. No in-memory runner is suitable in production.
    """
    def execute_idempotent(self, *, owner: str, project_id: str | None,
                           principal_id: str, request_id: str, payload_digest: str,
                           operation_id: str, command: Callable[[], dict]) -> dict: ...


class DomainRegistry:
    """Explicit owner bindings; method names come from frozen StudioApiPort."""
    def __init__(self):
        self._services: dict[str, object] = {}

    def register_owner(self, owner: str, service: object) -> None:
        if owner not in 'ABCDEFGHIJKLM' or len(owner) != 1:
            raise ValueError('unknown domain owner')
        if owner in self._services:
            raise ValueError('domain owner already registered')
        self._services[owner] = service

    def get(self, op: Operation) -> Callable:
        service = self._services.get(op.owner)
        handler = getattr(service, op.name, None) if service is not None else None
        if not callable(handler):
            raise ApiError('capability_unavailable', f'domain {op.owner} not connected')
        return handler

    def missing(self, contracts: FrozenContracts) -> list[str]:
        return [op.name for op in contracts.operations.values()
                if not callable(getattr(self._services.get(op.owner), op.name, None))]


class StudioOperatorService:
    def __init__(self, contracts: FrozenContracts, verifier: IdentityVerifier,
                 grants: ProjectGrantsReader, domains: DomainRegistry,
                 transactions: AtomicMutationRunner | None):
        self.contracts = contracts
        self.verifier = verifier
        self.grants = grants
        self.domains = domains
        self.transactions = transactions

    def execute(self, op: Operation, token: str, path_params: dict,
                query_params: dict, body: dict | None) -> dict:
        project_id = path_params.get('project_id')
        actor, principal = authenticated_actor(self.verifier, self.grants, token, project_id, op.auth)
        handler = self.domains.get(op)
        path_args = {key: path_params[key] for key in path_params}
        if op.name == 'listProjects':
            result = handler(actor)
            # Defense in depth: *do not* let an M projection list cross-project.
            if not isinstance(result, dict) or not isinstance(result.get('items'), list):
                raise ApiError('internal', 'invalid project listing')
            allowed = set(actor['allowed_project_ids'])
            result = {'items': [item for item in result['items']
                                if isinstance(item, dict) and item.get('project_id') in allowed]}
        elif op.mutation:
            if self.transactions is None:
                raise ApiError('capability_unavailable', 'transactional mutation store not connected')
            assert body is not None
            # Principal and full route context are included to avoid interpreting
            # one actor/operation's replay key as another's authorization.
            payload_digest = hashlib.sha256(json.dumps({
                'principal_id': principal.principal_id, 'operation': op.name,
                'path': path_args, 'body': body,
            }, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
               allow_nan=False).encode('utf-8')).hexdigest()
            def command() -> dict:
                # All domain mutation rules, owner-consumption and remote effect
                # journals are implemented by the exact owning service under C.
                generated = handler(actor, **path_args, request=body)
                # Validate *before* WG-C commits the idempotent result. A malformed
                # producer response must not become a durable replay success.
                self._validate_response(op, generated)
                return generated
            result = self.transactions.execute_idempotent(
                owner=op.owner, project_id=project_id,
                principal_id=principal.principal_id,
                request_id=body['request_id'], payload_digest=payload_digest,
                operation_id=op.name, command=command,
            )
        else:
            result = handler(actor, **path_args, **query_params)
        self._validate_response(op, result)
        return result

    def _validate_response(self, op: Operation, result: dict) -> None:
        if not isinstance(result, dict):
            raise ApiError('internal', 'domain returned non-object')
        # The authoritative schema is enforced on all responses incl. replays.
        try:
            self.contracts.validate(result, op.response_schema)
        except ApiError:
            raise ApiError('internal', 'domain returned schema-incompatible response') from None
