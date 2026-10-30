"""The one R9 composition root: 22 frozen ports, scoped state and no V1 facade.

A constructed runtime is a *local application*, not a native-host/QA proof.
External adapters and evidence validators are supplied by the trusted operator;
missing adapters cannot grant provider authority or make readiness successful.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping
import argparse
import importlib
import json
import os
import re
import signal
import threading
import time

from nomad_studio.contracts.runtime_ports_v1 import Result
from nomad_studio.state.r9_authority import R9Authority, DurableOutbox, inspect_database
from nomad_studio.state.r9_cas import FileCAS, RecordCAS
from nomad_studio.trust.evidence import ProviderEvidenceVerifier
from nomad_studio.trust.api import IdentityService
from nomad_studio.bibles.api import BibleRegistry
from nomad_studio.governance.api import AuthorityService
from nomad_studio.organization.api import ProjectRegistry
from nomad_studio.production.intake.api import BriefService
from nomad_studio.production.contract.api import FeatureContractCompiler
from nomad_studio.production.director.api import DirectorService
from nomad_studio.production.director.commands import CommandService
from nomad_studio.production.director.workflow import ProductionWorkflow
from nomad_studio.production.status.api import ProductionStatusService
from nomad_studio.production.workgraph.api import WorkGraphService
from nomad_studio.scheduler.api import SchedulingService
from nomad_studio.cabals.core.service import CabalService
from nomad_studio.cabals.council.api import CouncilService
from nomad_studio.registry.api import CapabilityService
from nomad_studio.single_operator.capabilities.host import WorkerHostService, AgentExecutionService
from nomad_studio.production.context.service import ContextService
from nomad_studio.production.context.adapter import ProjectAdapterService
from nomad_studio.artifacts.runtime import ArtifactService
from nomad_studio.production.repository.service import RepositoryService
from nomad_studio.publication.runtime import EffectService
from nomad_studio.production.quality.api import QualityService
from nomad_studio.oversight.api import OversightNoticeService
from nomad_studio.relay.dispatcher import OwnerCommandOutboxHandler

PORT_NAMES = (
    'IdentityPort', 'BiblePort', 'AuthorityPort', 'ProjectPort', 'BriefPort',
    'FeatureContractPort', 'DirectorPort', 'WorkGraphPort', 'SchedulingPort',
    'CabalPort', 'CouncilPort', 'CapabilityPort', 'WorkerHostPort',
    'AgentExecutionPort', 'ContextPort', 'ProjectAdapterPort', 'ArtifactPort',
    'RepositoryPort', 'EffectPort', 'QualityPort', 'CommandPort', 'OversightNoticePort',
)

# Required for live provider acceptance; dependency injection is only local wiring.
# An authenticated provider probe and TLS ingress are additional runtime gates.
_NATIVE_DEPENDENCIES = (
    'native_readback', 'native_outbox_readback', 'trust_anchors', 'native_worker_host',
    'native_task_authority', 'agent_authority', 'owner_gateway',
    'source_readback', 'source_provider', 'source_scope', 'source_reviewer',
    'runner', 'quality_proofs', 'native_permission_reader',
    'context_source', 'adapter_registry', 'effect_provider',
    'effect_scope', 'effect_owner_auth', 'immutable_request_reader',
    'oversight_assignment_reader', 'oversight_notice_sink', 'native_event_delivery',
    'compliance_verifier', 'native_host_journal', 'artifact_author_identity',
    'adapter_bible_verifier', 'accepted_quality_outcomes',
)


class ProviderUnavailable(RuntimeError):
    """A required provider is absent; this is never proof or acceptance."""


class _Missing:
    __slots__ = ('name',)

    def __init__(self, name: str):
        self.name = name

    def __getattr__(self, member: str):
        def fail(*_args: Any, **_kwargs: Any) -> Any:
            raise ProviderUnavailable(f'{self.name}.{member}: native provider not configured')
        return fail

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ProviderUnavailable(f'{self.name}: native provider not configured')


class AuthenticatedIngress:
    """In-process owner ingress, always anchored to CAB-H native proof verification.

    No network entrypoint is offered unless a separately verified TLS transport
    terminates mTLS and calls this adapter with an authenticated proof reference.
    """
    def __init__(self, identity: IdentityService, commands: CommandService):
        self.identity, self.commands = identity, commands

    def submit(self, command: Mapping[str, Any], proof_ref: str) -> Result:
        if not isinstance(command, Mapping) or command.get('auth_proof_ref') != proof_ref:
            return Result('REJECTED', 'UNAUTHENTICATED')
        action_digest = command.get('request_digest')
        if not isinstance(action_digest, str):
            return Result('REJECTED', 'INVALID_INPUT')
        owner = self.identity.authenticate_owner(proof_ref, action_digest)
        if owner.status != 'OK' or owner.code != 'OK':
            return owner
        return self.commands.submit_owner_command(command, owner)


@dataclass
class StudioRuntime:
    state: R9Authority
    cas: FileCAS
    services: Mapping[str, Any]
    outbox: DurableOutbox
    ingress: AuthenticatedIngress
    bound_native: Mapping[str, bool]
    closed: bool = False
    owner_command_handler: Any = None
    native_event_delivery: Any = None
    gateway: Any = None
    readiness: Any = None
    provider_id: str | None = None

    def port(self, name: str) -> Any:
        if self.closed:
            raise RuntimeError('Studio runtime is closed')
        return self.services[name]

    def health(self) -> dict[str, Any]:
        if self.closed:
            return {'liveness': 'STOPPED', 'readiness': 'NOT_READY', 'reason': 'CLOSED'}
        unbound = sorted(name for name, present in self.bound_native.items() if not present)
        pending = self.state.outbox_rows('PENDING')
        unknown = self.state.outbox_rows('UNKNOWN')
        status = {'liveness': 'ALIVE', 'readiness': 'NOT_READY',
                  'readiness_reason': 'LIVE_PROVIDER_AND_TLS_ACCEPTANCE_NOT_VERIFIED',
                  'database': 'R9_FRESH_SCHEMA_VERIFIED',
                  'registered_port_count': len(self.services),
                  'missing_native_dependencies': unbound,
                  'pending_outbox_count': len(pending),
                  'unknown_external_count': len(unknown),
                  'recovered_unknown_count': self.state.recovered_unknown_count,
                  'fenced_lease_count': self.state.fenced_lease_count}
        # Provider presence, configuration and local unit tests are not live proof.
        # The existing CAB-H DeploymentReadiness must verify the native host,
        # and the actual mTLS gateway must be running before declaring READY.
        if not unbound and self.gateway is not None and self.readiness is not None and self.provider_id:
            from nomad_studio.gateway.api import GatewayServer
            if isinstance(self.gateway, GatewayServer) and self.gateway.running:
                try:
                    observation = self.readiness.check(self.provider_id)
                    if (observation.status == 'OK' and observation.code == 'OK'
                            and observation.record is not None
                            and observation.record.get('ready') is True
                            and isinstance(observation.receipt_ref, str)
                            and observation.receipt_ref):
                        status['readiness'] = 'READY'
                        status.pop('readiness_reason')
                except (OSError, TimeoutError, RuntimeError):
                    pass
        return status

    def start_gateway(self, *, tls_context: Any,
                      certificate_identities: Mapping[str, Mapping[str, str]],
                      audit: Callable[[Mapping[str, Any]], None],
                      host: str = '127.0.0.1', port: int = 8765) -> tuple[str, int]:
        """Expose existing R9 mTLS ingress; certificates never grant owner rights."""
        if self.closed or self.gateway is not None:
            raise RuntimeError('runtime closed or gateway already started')
        from nomad_studio.gateway.api import OwnerGateway, GatewayServer
        gate = OwnerGateway(identity=self.services['IdentityPort'],
                            commands=self.services['CommandPort'],
                            certificate_identities=certificate_identities, audit=audit)
        server = GatewayServer(gate, tls_context=tls_context, host=host, port=port)
        address = server.serve()
        self.gateway = server
        return address

    def run(self, stop: threading.Event, poll_seconds: float = 0.5) -> None:
        """Process only verified R9 runtime work; never replay UNKNOWN effects."""
        if not isinstance(stop, threading.Event) or not 0.05 <= poll_seconds <= 10:
            raise ValueError('invalid runtime stop event/poll interval')
        while not stop.is_set():
            if self.health()['readiness'] != 'READY':
                raise ProviderUnavailable('native readiness or authenticated mTLS ingress lost')
            for item in self.state.outbox_rows('PENDING')[:64]:
                result = self.dispatch_once(item['message_id'])
                if result.status not in ('OK', 'CONFLICT', 'UNKNOWN_EXTERNAL'):
                    raise ProviderUnavailable('outbox delivery blocked: ' + result.code)
            stop.wait(poll_seconds)

    def dispatch_once(self, message_id: str) -> Result:
        """One durable R9 outbox delivery; no replay of ambiguous effects.

        Native event delivery and exact native receipt readback must be
        installed before claiming. The CAB-J command handler reauthenticates
        the owner on every attempt. Uncertain deliveries stay UNKNOWN.
        """
        if self.closed:
            return Result('BLOCKED_LOCAL', 'LOCAL_RECOVERY_REQUIRED')
        if not callable(self.outbox._readback):
            return Result('NOT_VERIFIED', 'PROVIDER_NOT_VERIFIED')
        rows = self.state.read(lambda tx: tx.fetch_all(
            'SELECT * FROM v1_outbox WHERE message_id=? AND state=?',
            (message_id, 'PENDING')))
        if len(rows) != 1:
            return Result('CONFLICT', 'REVISION_CONFLICT')
        row = rows[0]
        if row['domain'] == 'production.commands' and row['event_type'] == 'EXECUTE_OWNER_COMMAND':
            sender = self.owner_command_handler.handle if self.owner_command_handler else None
        elif row['domain'] == 'production.director' and row['event_type'] == 'FEATURE_RUN_TRANSITION':
            sender = self.native_event_delivery
        else:
            return Result('REJECTED', 'INVALID_INPUT')
        if not callable(sender):
            return Result('NOT_VERIFIED', 'PROVIDER_NOT_VERIFIED')
        import hashlib
        body = row['payload_json']
        if hashlib.sha256(body.encode('utf-8')).hexdigest() != row['payload_digest']:
            return Result('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        try:
            envelope = json.loads(body)
        except (ValueError, TypeError):
            return Result('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        if (not isinstance(envelope, dict) or envelope.get('domain') != row['domain']
                or envelope.get('event_type') != row['event_type']):
            return Result('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        if not self.outbox.claim(message_id):
            return Result('CONFLICT', 'REVISION_CONFLICT')
        try:
            result = sender(envelope)
            if (not isinstance(result, Result) or result.status != 'OK'
                    or result.code != 'OK' or not result.receipt_ref):
                self.outbox.mark_unknown(message_id)
                return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
            if self.outbox.acknowledge(message_id, provider_receipt_ref=result.receipt_ref):
                return Result('OK', 'OK', {'message_id': message_id}, result.receipt_ref)
        except (OSError, TimeoutError, RuntimeError, ValueError):
            self.outbox.mark_unknown(message_id)
        return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                if self.gateway is not None:
                    self.gateway.close()
            finally:
                self.state.close()

    def __enter__(self) -> 'StudioRuntime':
        if self.closed:
            raise RuntimeError('runtime closed')
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def open_studio(*, sqlite_path: str | Path, cas_path: str | Path | None = None,
                bindings: Mapping[str, Any] | None = None,
                clock_us: Callable[[], int] | None = None) -> StudioRuntime:
    """Build the new-only R9 runtime from the actual frozen port implementations.

    `bindings` is trusted process-local injection, never user/agent request
    input. Absent external proof cannot turn into a successful operation.
    """
    if bindings is not None and not isinstance(bindings, Mapping):
        raise TypeError('trusted native bindings must be a mapping')
    injected = dict(bindings or {})
    if clock_us is None:
        clock_us = lambda: time.time_ns() // 1000
    if not callable(clock_us):
        raise TypeError('trusted clock is required')

    def native(name: str) -> Any:
        return injected.get(name) if injected.get(name) is not None else _Missing(name)

    state = R9Authority(sqlite_path)
    try:
        cas = FileCAS(cas_path if cas_path is not None else Path(sqlite_path).absolute().parent / 'cas')
        owner = {name: state.capability(name) for name in (
            'CAB-A','CAB-B','CAB-C','CAB-D','CAB-E','CAB-F','CAB-G','CAB-H','CAB-I','CAB-J')}
        outbox = DurableOutbox(state, clock_us, readback=injected.get('native_outbox_readback'))
        reader = injected.get('native_readback')
        anchors = injected.get('trust_anchors') or {}
        verifier = ProviderEvidenceVerifier(reader if callable(reader) else lambda _ref: None, anchors)
        identity = IdentityService(verifier=verifier, store=owner['CAB-H'], clock_us=clock_us,
            leases=native('native_task_authority'), permission_reader=native('native_permission_reader'))
        bible = BibleRegistry(owner['CAB-D'], native('source_readback'), native('owner_gateway'))
        authority = AuthorityService(owner['CAB-D'], native('compliance_verifier'),
                                     native('owner_gateway'), clock_us)
        projects = ProjectRegistry(owner['CAB-B'])
        briefs = BriefService(owner['CAB-B'], clock_us)
        workgraph = WorkGraphService(owner['CAB-C'], rework_guard=injected.get('rework_guard'),
                                     plan_guard=injected.get('plan_guard'))
        scheduling = SchedulingService(owner['CAB-C'], clock=clock_us,
            allocation_guard=injected.get('allocation_guard'), placement_guard=injected.get('placement_guard'),
            checkpoint_guard=injected.get('checkpoint_guard'), recovery_guard=injected.get('recovery_guard'),
            completion_guard=injected.get('completion_guard'))
        cabals = CabalService(owner['CAB-A'], bible, authority, clock_us=clock_us,
            authenticated_worker=injected.get('authenticated_worker') or (lambda: None))
        council = CouncilService(db=owner['CAB-F'], records=RecordCAS(cas), bible=bible,
                                 authority=authority, clock_us=clock_us)
        capabilities = CapabilityService({'reader':owner['CAB-G'], 'writer':owner['CAB-G'],
            'clock_us': clock_us, 'installed_skill_reader':injected.get('installed_skill_reader'),
            'worker_observer':injected.get('worker_observer'), 'active_bible_reader':bible,
            'adapter_reader':injected.get('adapter_reader'),
            'feature_budget_reader':injected.get('feature_budget_reader'),
            'provider_billing_reader':injected.get('provider_billing_reader')})
        host = WorkerHostService(store=owner['CAB-H'], verifier=verifier,
            host=injected.get('native_worker_host'), journal=native('native_host_journal'),
            task_authority=native('native_task_authority'), clock_us=clock_us)
        agents = AgentExecutionService(host, authority=native('agent_authority'))
        artifacts = ArtifactService(owner['CAB-I'], cas, native('artifact_author_identity'), clock_us)
        repository = RepositoryService(owner['CAB-I'], cas, native('source_provider'),
            native('source_scope'), native('source_reviewer'), clock_us)
        adapter = ProjectAdapterService(owner['CAB-I'], cas, native('adapter_bible_verifier'),
            native('source_provider'), native('adapter_registry'), native('runner'), clock_us)
        quality = QualityService(store=owner['CAB-E'], artifacts=artifacts, repository=repository,
            project_adapter=adapter, workgraph=workgraph, proofs=native('quality_proofs'),
            result_type=Result, clock_us=clock_us)
        context = ContextService(owner['CAB-I'], cas, bible, native('context_source'),
            native('accepted_quality_outcomes'), clock_us)
        feature_contracts = FeatureContractCompiler(owner['CAB-B'], bible, authority,
            identity, extractor=injected.get('semantic_extractor'), context_port=context)
        director = DirectorService(owner['CAB-J'], outbox=outbox,
            observations=injected.get('director_observations') or {}, clock_us=clock_us)
        effects = EffectService(owner['CAB-I'], cas, native('effect_provider'), native('effect_scope'),
            native('effect_owner_auth'), clock_us)
        status = ProductionStatusService(owner['CAB-J'])
        payload_reader = native('immutable_request_reader')
        workflow = ProductionWorkflow(director=director, brief_port=briefs, bible_port=bible,
            contract_port=feature_contracts, context_port=context, graph_port=workgraph,
            capability_port=capabilities, scheduling_port=scheduling, payload_reader=payload_reader)
        commands = CommandService(owner['CAB-J'], outbox=outbox, status=status,
            director=director, workflow=workflow, payload_reader=payload_reader,
            delegates=injected.get('owner_command_delegates'), control=injected.get('command_control'),
            clock_us=clock_us)
        oversight = OversightNoticeService(verifier=verifier,
            assignment_reader=native('oversight_assignment_reader'),
            sink=native('oversight_notice_sink'), clock_us=clock_us)
        instances = (identity, bible, authority, projects, briefs, feature_contracts,
            director, workgraph, scheduling, cabals, council, capabilities,
            host, agents, context, adapter, artifacts, repository, effects, quality,
            commands, oversight)
        services = MappingProxyType(dict(zip(PORT_NAMES, instances, strict=True)))
        bound = MappingProxyType({name:injected.get(name) is not None for name in _NATIVE_DEPENDENCIES})
        return StudioRuntime(state, cas, services, outbox,
                             AuthenticatedIngress(identity, commands), bound,
                             owner_command_handler=OwnerCommandOutboxHandler(
                                 command_port=commands, identity_port=identity),
                             native_event_delivery=injected.get('native_event_delivery'),
                             readiness=injected.get('deployment_readiness'),
                             provider_id=injected.get('provider_id'))
    except BaseException:
        state.close()
        raise


def _config(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('R9 production config path must be an existing regular file')
    raw = json.loads(path.read_text('utf-8'))
    if not isinstance(raw, dict) or set(raw) != {'sqlite_path', 'cas_path'}:
        raise ValueError('R9 config requires only sqlite_path and cas_path; provider bindings are supplied natively')
    if not all(isinstance(raw[x], str) and raw[x] and Path(raw[x]).is_absolute() for x in raw):
        raise ValueError('R9 config storage paths must be absolute')
    return raw


# Executable startup is an operator-selected native integration, not a writable
# runtime config field. The operator's installed Python module must construct
# the existing R9 runtime with genuine provider bindings and start its mTLS
# gateway. The root then verifies readiness before processing any work.
_FACTORY_NAME = re.compile(r'^[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*:[A-Za-z_][A-Za-z_0-9]*$')


def _load_runtime_factory(name: str) -> Callable[..., StudioRuntime]:
    if not isinstance(name, str) or not _FACTORY_NAME.fullmatch(name):
        raise ValueError('runtime factory must be an installed module:function')
    module, symbol = name.split(':', 1)
    factory = getattr(importlib.import_module(module), symbol, None)
    if not callable(factory):
        raise ValueError('operator runtime factory is not callable')
    return factory


def _run_configured_runtime(factory: Callable[..., StudioRuntime],
                            config: Mapping[str, str]) -> int:
    runtime = factory(sqlite_path=config['sqlite_path'], cas_path=config['cas_path'])
    # `python -m nomad_studio.app` loads this module as __main__; native
    # factories import nomad_studio.app by its installed canonical name.
    canonical_runtime = importlib.import_module('nomad_studio.app').StudioRuntime
    if not isinstance(runtime, canonical_runtime):
        raise ValueError('operator runtime factory must return StudioRuntime')
    with runtime:
        if (runtime.state.path != Path(config['sqlite_path']).absolute()
                or runtime.cas.root != Path(config['cas_path']).absolute()):
            raise ValueError('operator runtime factory returned a different authority')
        status = runtime.health()
        print(json.dumps(status, sort_keys=True), flush=True)
        if status['readiness'] != 'READY':
            return 2
        stop = threading.Event()

        def terminate(_signum: int, _frame: Any) -> None:
            stop.set()

        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        runtime.run(stop)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Nomad Studio R9 local authority and service readiness')
    parser.add_argument('--config', default=os.environ.get('NOMAD_STUDIO_CONFIG'),
                        help='path to private JSON config, or NOMAD_STUDIO_CONFIG')
    parser.add_argument('--doctor', action='store_true', help='inspect local R9 readiness and exit')
    parser.add_argument('--runtime-factory', default=os.environ.get('NOMAD_STUDIO_RUNTIME_FACTORY'),
                        help='operator-installed module:function that starts R9 with native providers and mTLS')
    args = parser.parse_args(argv)
    if not args.config:
        parser.error('an explicit --config or NOMAD_STUDIO_CONFIG is required')
    try:
        config = _config(Path(args.config))
        if args.doctor:
            status = inspect_database(config['sqlite_path'])
            status['registered_port_count'] = len(PORT_NAMES)
            status['readiness'] = 'NOT_READY'
            status['readiness_reason'] = 'NATIVE_RUNTIME_NOT_OBSERVED'
            print(json.dumps(status, sort_keys=True), flush=True)
            return 2
        if not args.runtime_factory:
            print(json.dumps({'liveness':'STOPPED','readiness':'NOT_READY',
                              'reason':'NATIVE_RUNTIME_FACTORY_NOT_CONFIGURED'}), flush=True)
            return 2
        return _run_configured_runtime(_load_runtime_factory(args.runtime_factory), config)
    except Exception as exc:
        # Do not leak provider credentials, filesystem paths or transport details.
        print(json.dumps({'liveness':'STOPPED','readiness':'NOT_READY','reason':type(exc).__name__}), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
