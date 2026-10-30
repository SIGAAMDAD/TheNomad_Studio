"""Verified owner registration for the frozen 72-operation Studio operator API.

Constructed services are supplied by the trusted deployer. No mock service,
synthetic receipt, model-runtime launcher or implicit worker is created here.
The composition root verifies route coverage, not live external capabilities.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from studio.api import DomainRegistry, FrozenContracts, StudioOperatorService
from studio.api.errors import ApiError
from studio.api.events import EventPageService
from studio.api.identity import IdentityVerifier, ProjectGrantsReader
from studio.api.service import AtomicMutationRunner
from studio.agents.operations import WorkerOperations

from .assurance_commands import AssuranceCommands
from .composition import StudioComposition
from .governance_commands import GovernanceCommands
from .operations_commands import OperationsCommands
from .organization_commands import OrganizationCommands
from .production_commands import ProductionCommands
from .provider_commands import ProviderCommands


def assemble_operator_service(
    *, composition: StudioComposition, governance: GovernanceCommands,
    organization: OrganizationCommands, production: ProductionCommands,
    assurance: AssuranceCommands, operations: OperationsCommands,
    providers: ProviderCommands, workers: WorkerOperations,
    events: EventPageService, verifier: IdentityVerifier,
    grants: ProjectGrantsReader, transactions: AtomicMutationRunner,
    contracts_root: str | Path,
) -> StudioComposition:
    """Bind real A/B/D/E/F/G/I/J/M owners before the HTTP listener is opened.

    All domain services must have been constructed with trusted, durable ports
    by the deploying application. The function does not infer backend health
    from successful method registration or enable the server itself.
    """
    if not isinstance(composition, StudioComposition):
        raise TypeError('StudioComposition required')
    if composition.operator_service is not None:
        raise ValueError('operator service already attached')
    services = {
        'A': (governance, GovernanceCommands),
        'B': (organization, OrganizationCommands),
        'D': (production, ProductionCommands),
        'E': (assurance, AssuranceCommands),
        'F': (operations, OperationsCommands),
        'G': (providers, ProviderCommands),
        'I': (workers, WorkerOperations),
        'J': (events, EventPageService),
        'M': (composition, StudioComposition),
    }
    for owner, (instance, expected_type) in services.items():
        if not isinstance(instance, expected_type):
            raise TypeError(f'owner {owner} requires an actual {expected_type.__name__}')
        # An adapter's own method may remain callable when its actual delegated
        # implementation is missing or replaced. Check the underlying domain
        # before allowing a 72-operation registry to report a complete service.
        if owner in ('A', 'B', 'D', 'E'):
            delegated = getattr(instance, 'service', None)
            if any(not callable(getattr(delegated, name, None))
                   for name in instance.operation_ids):
                raise ApiError('capability_unavailable',
                               f'domain {owner} has missing implementation methods')
    if (not callable(getattr(verifier, 'verify_bearer', None))
            or not callable(getattr(grants, 'granted_project_ids', None))
            or not callable(getattr(transactions, 'execute_idempotent', None))):
        raise TypeError('operator authentication, grants and durable mutation runner required')

    contracts = FrozenContracts(contracts_root)
    if len(contracts.operations) != 72:
        raise ValueError('frozen operator catalog must contain 72 operations')
    registry = DomainRegistry()
    for owner, (instance, _) in services.items():
        registry.register_owner(owner, instance)
    missing = registry.missing(contracts)
    if missing:
        raise ApiError('capability_unavailable', 'operator contracts missing: '+', '.join(sorted(missing)))
    return replace(composition, operator_service=StudioOperatorService(
        contracts, verifier, grants, registry, transactions))
