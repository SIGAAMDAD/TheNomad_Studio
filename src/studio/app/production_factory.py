"""Single production assembly for the Studio's frozen 72-operation operator API.

No model workers, simulation backends or synthetic GitHub results are created.
An authenticated deployer supplies only external provider/authority adapters;
all domain state is assembled from the SAME C-owned APSW writer.

Set STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment
and STUDIO_TRUSTED_PORTS_FACTORY to a trusted importable module:function.
The provider factory must return a TrustedPorts instance with real installed
provider adapters; absence refuses startup instead of running a fake Studio.
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from studio.agents.operations import WorkerOperations
from studio.api import EventPageService
from studio.api.identity import PrincipalProvisioner
from studio.assurance.service import AssuranceService
from studio.integrations.api_ports import GOperatorPorts
from studio.operations.core import Operations
from studio.organization.service import OrganizationService
from studio.production.service import ProductionService
from studio.workforce import Workforce

from studio.storage.assurance import AssuranceAPSWStore
from studio.storage.governance import GovernanceAPSWService
from studio.storage.governance_ports import (VerifiedGovernanceBlobSource,
                                             CanonImpactAPSWCatalog)
from studio.storage.grants import CurrentGrants
from studio.storage.integration_state import IntegrationAPSWStore
from studio.storage.operations import OperationsAPSWRunner
from studio.storage.organization import OrganizationAPSWStore
from studio.storage.production import ProductionAPSWStorage
from studio.storage.projects import ProjectAPSWStore
from studio.storage.principals import PrincipalAPSWStore
from studio.storage.recovery import RecoveryAPSWStore
from studio.storage.releases import ReleaseAPSWStore
from studio.storage.reservations import AtomicBudgetReservations
from studio.storage.workforce import WorkforceStorage

from .assurance_commands import AssuranceCommands
from .build_recovery import BuildRecoveryPump
from .composition import StudioComposition
from .errors import StudioError
from .governance_commands import GovernanceCommands
from .integration_commands import IntegrationService
from .lifecycle import Component, StudioRuntime
from .operations_commands import OperationsCommands
from .operator_wiring import assemble_operator_service
from .organization_commands import OrganizationCommands
from .production_commands import ProductionCommands
from .provider_commands import ProviderCommands
from .projects import ProjectService
from .releases import RecoveryCoordinator, ReleaseCoordinator


@dataclass(frozen=True)
class TrustedPorts:
    """Trusted deployment inputs, never supplied through operator HTTP or GitHub.

    The external provider objects must perform actual authenticated I/G/J readback,
    independent QA, owner policy, and deterministic tool-host execution.
    """
    verifier: Any
    capability_probe: Any
    organization_policy: Any
    organization_source: Any
    organization_trust: Any
    organization_host: Any
    production_authorization: Any
    production_briefs: Any
    production_acceptance: Any
    production_progress: Any
    production_stop: Any
    assurance_original: Any
    assurance_reviewers: Any
    assurance_validators: Any
    report_profiles: Any
    report_blobs: Any
    report_relay: Any
    provider_for_project: Callable[[str], GOperatorPorts]
    effect_readback: Any
    worker_authorize: Any
    verify_worker_receipt: Any
    release_evidence: Any
    release_publisher: Any
    integration_roster: Any
    integration_submissions: Any
    integration_qa: Any
    integration_remote: Any
    verified_source_revision: Any
    startup_recovery: Any
    bootstrap_credentials: tuple[str, ...] = ()
    command_delivery: Any | None = None


class ProjectProviderRouter:
    """Use one authenticated G provider binding per project, never a global token."""
    def __init__(self, factory: Callable[[str], GOperatorPorts]):
        if not callable(factory):
            raise TypeError("authenticated project provider factory required")
        self._factory = factory

    def _provider(self, project_id: str) -> GOperatorPorts:
        provider=self._factory(project_id)
        if (not isinstance(provider,GOperatorPorts)
                or provider.binding.project_id!=project_id):
            raise StudioError("forbidden","G provider is not bound to requested project")
        return provider

    def list_artifacts(self, *, project_id):
        return self._provider(project_id).list_artifacts(project_id=project_id)

    def get_artifact(self, *, project_id, artifact_id):
        return self._provider(project_id).get_artifact(
            project_id=project_id,artifact_id=artifact_id)

    def attach_connector(self, *, project_id, **request):
        return self._provider(project_id).attach_connector(project_id=project_id,**request)

    def run_build(self, *, project_id, **request):
        return self._provider(project_id).run_build(project_id=project_id,**request)

    def observe_effect(self, *, project_id, **request):
        return self._provider(project_id).observe_effect(project_id=project_id,**request)

    def reconcile_effect(self, *, project_id, **request):
        return self._provider(project_id).reconcile_effect(project_id=project_id,**request)


class IndexedEventReader:
    def __init__(self, writer: WorkforceStorage):
        self.writer=writer

    def scan_project_events(self, *, project_id: str, after: int, limit: int) -> list[dict]:
        if (type(after) is not int or after<0
                or type(limit) is not int or not 1<=limit<=500):
            raise StudioError("invalid","audit cursor or limit invalid")
        return self.writer.read(project_id,lambda tx:tx.all(
            "SELECT project_id,event_seq,event_type,subject_id,payload_digest "
            "FROM audit_events INDEXED BY idx_events_cursor WHERE project_id=? "
            "AND event_seq>? ORDER BY event_seq LIMIT ?",
            (project_id,after,limit)))


def _validate_external(ports: TrustedPorts) -> None:
    if not isinstance(ports,TrustedPorts):
        raise TypeError("trusted deployment must supply a TrustedPorts instance")
    required={
        "verifier":("verify_bearer",),
        "capability_probe":("probe",),
        "organization_policy":("check_membership","check_tool_grants"),
        "organization_source":("get",),
        "organization_trust":("verify_package",),
        "organization_host":("tool_available","validator_passed"),
        "production_authorization":("require",),
        "production_briefs":("get_verified",),
        "production_acceptance":("require_accepted",),
        "production_progress":("require_stage",),
        "production_stop":("notify_task_canceled",),
        "assurance_original":("get","put_verified"),
        "assurance_reviewers":("choose",),
        "assurance_validators":("run",),
        "report_profiles":("load_verified",),
        "report_blobs":("store_verified",),
        "report_relay":("queue_report",),
        "release_evidence":("verified_source_qa_ci","verified_canon_snapshot",
                            "verified_artifacts","verified_release_digest","approved_exact_effect"),
        "release_publisher":("observe_exact","publish"),
        "integration_submissions":("load_submission",),
        "integration_qa":("verify",),
        "integration_remote":("verify_exact",),
        "startup_recovery":("recover",),
    }
    for name, methods in required.items():
        entry=getattr(ports,name)
        if not callable(getattr(entry,"for_writer",None)) and any(
                not callable(getattr(entry,method,None)) for method in methods):
            raise TypeError(f"{name} missing trusted operation: {methods}")
    for name in ("provider_for_project","effect_readback",
                 "worker_authorize","verify_worker_receipt",
                 "verified_source_revision"):
        if not callable(getattr(ports,name)):
            raise TypeError(f"{name} must be a callable trusted provider")
    if ports.command_delivery is not None and not (
            callable(getattr(ports.command_delivery,"for_writer",None))
            or all(callable(getattr(ports.command_delivery,method,None))
                   for method in ("start","stop"))):
        raise TypeError("configured GitHub outbox pump is invalid")
    if not hasattr(ports.integration_roster,"paths") or not hasattr(ports.integration_roster,"plan_sha256"):
        raise TypeError("verified integration ownership roster required")


def assemble_live_studio(*, database_path: Path, blob_root: Path,
                         source_root: Path, contracts_root: Path,
                         ports: TrustedPorts, fresh: bool = False) -> StudioComposition:
    """Real production assembly; refuse startup on absent capabilities.

    No transport is started until runtime recovery has completed. The HTTP
    listener is owned separately by cli.main and starts after runtime recovery.
    """
    _validate_external(ports)
    writer=WorkforceStorage.open(database_path,fresh=fresh)
    try:
        # Credentials are trusted deployment configuration, never HTTP inputs.
        # J verifies identity and C persists the immutable principal before
        # M can create its first project and owner grant.
        provisioner=PrincipalProvisioner(ports.verifier,PrincipalAPSWStore(writer))
        for credential in ports.bootstrap_credentials:
            provisioner.provision_bearer(credential)
        governance=GovernanceCommands(GovernanceAPSWService(
            writer,blobs=VerifiedGovernanceBlobSource(writer,blob_root),
            impacts=CanonImpactAPSWCatalog(writer)))
        organization=OrganizationCommands(OrganizationService(
            OrganizationAPSWStore(writer),ports.organization_policy,
            source=ports.organization_source,trust=ports.organization_trust,
            host=ports.organization_host))
        def configured_port(value):
            if callable(getattr(value,"for_writer",None)):
                return value.for_writer(writer,blob_root)
            return value

        production=ProductionCommands(ProductionService(
            storage=ProductionAPSWStorage(writer),
            authorization=configured_port(ports.production_authorization),
            briefs=configured_port(ports.production_briefs),
            reservations=AtomicBudgetReservations(),
            acceptance=ports.production_acceptance,
            progress=ports.production_progress,stop=ports.production_stop))
        assurance=AssuranceCommands(AssuranceService(
            AssuranceAPSWStore(writer),ports.assurance_original,
            ports.assurance_reviewers,ports.assurance_validators))
        operations=OperationsCommands(
            Operations(OperationsAPSWRunner(writer)),
            profiles=configured_port(ports.report_profiles),
            blobs=configured_port(ports.report_blobs),
            relay=ports.report_relay)
        bound_provider=ports.provider_for_project
        if callable(getattr(bound_provider, "for_writer", None)):
            bound_provider=bound_provider.for_writer(writer,blob_root)
        providers=ProviderCommands(ProjectProviderRouter(
            bound_provider),effect_readback=ports.effect_readback)
        # Missed workflow notifications are reconciled from exact C effect
        # intents; this readback-only component never dispatches another build.
        build_recovery=BuildRecoveryPump(
            writer=writer,provider_for_project=bound_provider)
        workforce=Workforce(writer,require_action=ports.worker_authorize)
        workers=WorkerOperations(workforce,
            verify_receipt=ports.verify_worker_receipt)
        delivery=ports.command_delivery
        if delivery is not None and callable(getattr(delivery,"for_writer",None)):
            delivery=delivery.for_writer(writer,blob_root,workforce)
        audit=EventPageService(IndexedEventReader(writer))
        def verify_storage_integrity():
            if writer._connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise StudioError("internal", "APSW database integrity check failed")

        parts=[
            Component("C-APSW",recover=verify_storage_integrity,
                      start=lambda:None,stop=writer.close),
            Component("G-I-startup-reconciliation",
                      recover=lambda:configured_port(ports.startup_recovery).recover(),
                      start=lambda:None,stop=lambda:None),
        ]
        if delivery is not None:
            parts.append(Component("G-I-command-publisher",
                                   recover=lambda:None,start=delivery.start,stop=delivery.stop))
        parts.append(Component("G-Actions-build-reconciliation",
                               recover=lambda:None,
                               start=build_recovery.start,stop=build_recovery.stop))
        runtime=StudioRuntime(parts)
        app=StudioComposition(
            projects=ProjectService(ProjectAPSWStore(writer),ports.capability_probe),
            releases=ReleaseCoordinator(ReleaseAPSWStore(writer),
                                        ports.release_evidence,ports.release_publisher),
            recovery=RecoveryCoordinator(RecoveryAPSWStore(writer)),
            runtime=runtime,
            integrations=IntegrationService(
                roster=ports.integration_roster,tree_root=source_root,
                submissions=ports.integration_submissions,
                state=IntegrationAPSWStore(writer,ports.verified_source_revision),
                qa=ports.integration_qa,remote=ports.integration_remote))
        return assemble_operator_service(
            composition=app,governance=governance,organization=organization,
            production=production,assurance=assurance,operations=operations,
            providers=providers,workers=workers,events=audit,
            verifier=ports.verifier,grants=CurrentGrants(writer),
            transactions=None,contracts_root=contracts_root)
    except BaseException:
        writer.close()
        raise


def build_from_environment() -> StudioComposition:
    """Configured service entrypoint for cli.py, not a browser-chat launcher."""
    location=os.environ.get("STUDIO_TRUSTED_PORTS_FACTORY","")
    if ":" not in location:
        raise StudioError("capability_unavailable",
                          "STUDIO_TRUSTED_PORTS_FACTORY not configured")
    module,func=location.split(":",1)
    if not module or not func.isidentifier():
        raise StudioError("invalid","trusted factory import invalid")
    provider=getattr(importlib.import_module(module),func)
    ports=provider()
    if ports.command_delivery is None:
        raise StudioError("capability_unavailable",
                          "durable GitHub command outbox publisher is not configured")
    db=os.environ.get("STUDIO_APSW_DATABASE")
    blobs=os.environ.get("STUDIO_BLOB_ROOT")
    source=os.environ.get("STUDIO_SOURCE_ROOT")
    if not all((db,blobs,source)):
        raise StudioError("capability_unavailable",
                          "Studio durable database, blob root and source root required")
    return assemble_live_studio(
        database_path=Path(db),blob_root=Path(blobs),
        source_root=Path(source),contracts_root=Path(
            os.environ.get("STUDIO_CONTRACT_DIR","CONTRACTS")),
        ports=ports,fresh=False)
