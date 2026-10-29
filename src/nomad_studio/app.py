"""Integration-owned composition root for the one-wave greenfield Studio.

One SQLite owner, frozen Component facades, explicitly injected trusted providers.
Nothing here authenticates a prompt, launches a model or claims an external effect.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Mapping

from nomad_studio.contracts.v1 import CommandEnvelope, Component, Outcome, QueryEnvelope
from nomad_studio.state.api import open_store, build_component as state_component
from nomad_studio.trust.api import build_component as trust_component
from nomad_studio.source.api import build_component as source_component
from nomad_studio.registry.api import build_component as registry_component
from nomad_studio.organization.api import build_component as organization_component
from nomad_studio.budget.api import build_component as budget_component
from nomad_studio.scheduler.api import build_component as scheduler_component
from nomad_studio.relay.api import build_component as relay_component
from nomad_studio.shift.api import build_component as shift_component
from nomad_studio.review.api import build_component as review_component
from nomad_studio.publication.api import build_component as publication_component
from nomad_studio.artifacts.api import build_component as artifacts_component
from nomad_studio.knowledge.api import build_component as knowledge_component
from nomad_studio.archive.api import build_component as archive_component
from nomad_studio.oversight.api import build_component as oversight_component
from nomad_studio.operations.api import build_component as operations_component
from nomad_studio.console.api import build_component as console_component
from nomad_studio.deployment.api import build_component as deployment_component
from nomad_studio.verification.api import build_component as verification_component
from nomad_studio.gateway.api import build_transport, store_audit_sink
from nomad_studio.integration_auth import InternalAuthorization
from nomad_studio.integration_wire import WireComponent, gateway_facades
from nomad_studio.single_operator.personas.api import build_component as persona_component
from nomad_studio.single_operator.ownercontrol.api import build_component as ownercontrol_component
from nomad_studio.single_operator.convergence.api import build_component as convergence_component
from nomad_studio.single_operator.capabilities.api import build_component as capabilities_component
from nomad_studio.hr_bridge.api import build_component as hr_bridge_component
from nomad_studio.creative_authority.policy.api import build_component as creative_policy_component
from nomad_studio.creative_authority.impact.api import build_component as creative_impact_component
from nomad_studio.creative_authority.console.api import build_component as creative_console_component
from nomad_studio.creative_authority.enforcement.api import build_component as creative_enforcement_component
from nomad_studio.creative_authority.bindings.evaluation import build_component as artifact_bindings_component
from nomad_studio.creative_authority.bindings.registry import BindingRegistry

DOMAIN_ORDER = (
    'state', 'trust', 'registry', 'source', 'organization', 'budget',
    'scheduler', 'relay', 'shift', 'review', 'publication', 'artifacts',
    'knowledge', 'archive', 'oversight', 'operations', 'console',
    'deployment', 'verification',
)

# These Studio V1 extensions are assembled internally. They are NOT implicitly
# exposed on the A-S public gateway; each needs explicit trusted policy/port
# registration before receiving external commands. Private Oversight modules
# remain separate and are never imported into this process.
EXTENSION_ORDER = (
    'persona', 'ownercontrol', 'convergence', 'capabilities', 'hr_bridge',
    'creative_policy', 'creative_impact', 'creative_console',
    'creative_enforcement', 'artifact_bindings',
)


class ComponentRef:
    """A late-bound frozen Component reference for genuine inter-service dependency cycles.

    Until the owning implementation has been constructed, the reference FAILS CLOSED.
    This isn't a fake external provider and never manufactures a positive receipt.
    """
    def __init__(self, name: str):
        self.name = name
        self._component: Component | None = None

    def bind(self, component: Component) -> None:
        if self._component is not None or component.name != self.name:
            raise ValueError('component reference rebound or name mismatch')
        self._component = component

    def execute(self, command: CommandEnvelope, identity: Any) -> Outcome:
        if self._component is None:
            return Outcome('NOT_RUN', 'NOT_CONFIGURED')
        # Sisters authored in the one-wave use a mix of internal plain verbs
        # and frozen dotted envelope operations. Reconcile at this root port.
        if command.operation.startswith(self.name + '.'):
            return WireComponent(self._component).execute(command, identity)
        return self._component.execute(command, identity)

    def query(self, query: QueryEnvelope, identity: Any) -> Outcome:
        if self._component is None:
            return Outcome('NOT_RUN', 'NOT_CONFIGURED')
        if query.operation.startswith(self.name + '.'):
            return WireComponent(self._component).query(query, identity)
        return self._component.query(query, identity)


@dataclass
class StudioRuntime:
    store: Any
    components: Mapping[str, Component]
    gateway: Any | None = None
    closed: bool = False
    extensions: Mapping[str, object] = field(default_factory=dict)

    def component(self, domain: str) -> Component:
        return self.components[domain]

    def extension(self, name: str) -> object:
        """Internal trusted-process extension; not an authenticated gateway route."""
        if self.closed:
            raise RuntimeError('runtime is closed')
        return self.extensions[name]

    def open_gateway(self, *, tls_context: Any, certificate_identities: Mapping[str, Any],
                     host: str = '127.0.0.1', port: int = 0, **settings: Any) -> Any:
        """Explicit mTLS-only host operation; never opens a socket during composition."""
        if self.closed or self.gateway is not None:
            raise RuntimeError('runtime closed or gateway already opened')
        transport = build_transport({
            'trust_component': self.components['trust'],
            'components': gateway_facades(self.components),
            'tls_context': tls_context,
            'certificate_identities': certificate_identities,
            'audit_sink': store_audit_sink(self.store),
            'host': host,
            'port': port,
            **settings,
        })
        self.gateway = transport
        return transport

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                if self.gateway is not None:
                    self.gateway.close()
            finally:
                self.store.close()

    def __enter__(self) -> 'StudioRuntime':
        if self.closed:
            raise RuntimeError('runtime is closed')
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def open_studio(*, sqlite_path: str | Path, owner_principal_id: str,
                trusted_issuer_ids: tuple[str, ...],
                ports: Mapping[str, object] | None = None,
                settings: Mapping[str, object] | None = None) -> StudioRuntime:
    """Compose 19 public base facades and 10 internal Studio V1 extensions.

    `ports` are trusted, process-local operator wiring, NOT HTTP/chat input. Missing
    external provider ports are left unconfigured, so source, QA, archive, connector
    and Oversight operations fail closed. No default simulated verification.
    """
    if not isinstance(owner_principal_id, str) or not owner_principal_id.startswith('owner/') or not owner_principal_id[6:]:
        raise ValueError('explicit trusted owner principal required')
    if not trusted_issuer_ids or any(not isinstance(x, str) or not x for x in trusted_issuer_ids):
        raise ValueError('explicit trusted issuer allowlist required')
    if not isinstance(ports or {}, Mapping) or not isinstance(settings or {}, Mapping):
        raise ValueError('ports and settings must be mappings')
    ports = dict(ports or {})
    settings = dict(settings or {})

    def port(key: str) -> Any:
        return ports.get(key)

    def clock_us() -> int:
        return time.time_ns() // 1000

    clock = port('clock_us') or clock_us
    if not callable(clock):
        raise ValueError('trusted clock port must be callable')
    store_config = dict(settings.get('state', {}))
    # A new Studio V1 database always uses the single pinned V1 schema.
    store_config['path'] = str(Path(sqlite_path).resolve())
    store = open_store(store_config)
    refs = {name: ComponentRef(name) for name in DOMAIN_ORDER}
    comps: dict[str, Component] = {}

    def add(name: str, component: Component) -> None:
        if not callable(getattr(component, 'query', None)) or not callable(getattr(component, 'execute', None)):
            raise RuntimeError(f'{name} lacks required facade')
        refs[name].bind(component)
        comps[name] = component

    try:
        # The only direct implementation imports occur here in the composition root.
        add('state', state_component({'store': store, 'snapshot_dir': port('snapshot_dir')}))
        slot_authority = port('slot_authority')
        if slot_authority is not None and not callable(slot_authority):
            raise ValueError('slot authority must be a trusted callable')
        # Absent external authority means no worker slot can be authorized.
        slot_authority = slot_authority or (lambda _worker_id: None)
        add('trust', trust_component({'store': store, 'slot_authority': slot_authority, 'clock_us': clock}))
        internal = InternalAuthorization(store=store, trust=comps['trust'],
                                         slot_authority=slot_authority,
                                         clock_us=clock, review_verifier=port('review_authority'))
        add('registry', registry_component({
            'skill_pins': port('skill_pins') or {}, 'skill_loader': port('skill_loader'),
            'slot_authorizer': port('slot_authorizer'), 'fleet_observer': port('fleet_observer'), 'now_us': clock,
        }))
        add('source', source_component({
            'store': store, 'source_reader': port('source_reader'),
            'scheduler': refs['scheduler'], 'clock_us': clock,
        }))
        add('organization', organization_component({
            'state': store, 'owner_principal_id': owner_principal_id,
            'trusted_issuer_ids': trusted_issuer_ids, 'source': refs['source'],
            'oversight': refs['oversight'], 'clock_us': clock,
        }))
        add('budget', budget_component({'store': store, 'clock_us': clock,
                                        'billing_verifier': port('billing_verifier')}))
        add('scheduler', scheduler_component({
            'store': store, 'source': refs['source'], 'review': refs['review'],
            'reconciliation': port('lease_reconciliation'), 'now_us': clock,
            'config': settings.get('scheduler', {}),
        }))
        add('relay', relay_component({
            'store': store, 'github': port('github_relay'),
            'authorize_worker': internal.authorize_relay_worker,
            'authorize_sender': internal.authorize_relay_sender,
            'verify_processing_receipt': internal.verify_relay_receipt,
            'clock_us': clock, 'webhook_secret': port('webhook_secret'),
        }))
        add('shift', shift_component({
            'store': store, 'trust': internal, 'scheduler': refs['scheduler'],
            'relay': refs['relay'], 'clock_us': clock,
        }))
        # The review authority epoch must come from a trusted live reader.
        # Without an independent issuer the review component still fails closed.
        epoch_reader = port('review_authority_epoch')
        if epoch_reader is None:
            epoch_reader = lambda: -1
        if not callable(epoch_reader):
            raise ValueError('review authority epoch must be callable')
        add('review', review_component({
            'store': store, 'source': refs['source'], 'trust': internal,
            'authority_epoch': epoch_reader,
            'assignment_epoch': internal.assignment_epoch,
            'clock_us': clock,
            'required_ci_checks': settings.get('required_ci_checks', ('unit-tests',)),
            'required_owner_decisions': settings.get('required_owner_decisions', ('architecture', 'canon')),
        }))
        add('publication', publication_component({
            'store': store, 'authorization': port('publication_authorization'),
            'github_transport': port('github_publication'),
            'drive_transport': port('drive_publication'),
            'artifact_reader': port('artifact_reader'),
            'clock_us': clock, 'quotas': settings.get('publication_quotas', {}),
        }))
        add('artifacts', artifacts_component({
            'store': store, 'readback': port('artifact_readback'),
            'authorize': port('artifact_authorize'),
            'review': refs['review'], 'publication': refs['publication'],
            'folder_registry': settings.get('folders', {}),
            'format_tools': port('format_tools') or {}, 'now_us': clock,
        }))
        add('knowledge', knowledge_component({
            'store': store, 'source': refs['source'], 'artifacts': refs['artifacts'],
            'service_identity': port('knowledge_service_identity'),
            'owner_principals': (owner_principal_id,),
        }))
        add('archive', archive_component({
            'state': store, 'archive_root': port('archive_root'),
            'authority': port('archive_authority'), 'approvals': port('archive_approvals'),
            'snapshot': port('archive_snapshot'), 'evidence': port('archive_evidence'),
            'cryptor': port('archive_cryptor'), 'witness_verifier': port('archive_witness'),
            'encrypted': settings.get('archive_encrypted', False),
        }))
        add('oversight', oversight_component({
            'store': store, 'oversight_backend': port('oversight_backend'),
            'enforcement_port': port('oversight_enforcer'),
            'trusted_issuer_ids': ports.get('oversight_issuer_ids', ()),
            'trusted_key_ids': ports.get('oversight_key_ids', ()), 'clock_us': clock,
        }))
        add('operations', operations_component({
            'store': store, 'observer': port('operations_observer'),
            'effects': port('operations_effects'), 'alerts': port('operations_alerts'),
        }))
        add('console', console_component({
            'store': store, 'scheduler': refs['scheduler'], 'operations': refs['operations'],
            'verification': refs['verification'], 'organization': refs['organization'],
            'registry': refs['registry'], 'trust': internal, 'shift': refs['shift'],
            'source': refs['source'], 'review': refs['review'],
            'slot_authority': slot_authority,
            'clock_us': clock,
        }))
        add('deployment', deployment_component({
            'config': settings.get('deployment'),
            'connector_probe_port': port('connector_probe'),
        }))
        add('verification', verification_component({
            'source_root': port('source_root') or str(Path(__file__).resolve().parents[2]),
            'evidence_dir': port('verification_evidence_dir'),
            'probes': port('verification_probes') or {},
            'fault_adapters': port('verification_faults') or {},
            'load_driver': port('verification_load_driver'),
            'probe_mode': 'INTEGRATED' if port('verification_probes') else 'FAKE',
            'external_verifier': port('verification_external_verifier'),
        }))
        assert tuple(comps) == DOMAIN_ORDER
        # Only operator-supplied process-local dependency ports may enable an
        # external capability. Missing ports stay absent and mutations fail
        # closed; no authority is manufactured from prompt text or test mocks.
        extension_ports = port('extension_ports') or {}
        if not isinstance(extension_ports, Mapping) or set(extension_ports) - set(EXTENSION_ORDER):
            raise ValueError('invalid extension port registry')
        def extension_deps(name: str) -> dict[str, object]:
            supplied = extension_ports.get(name, {})
            if not isinstance(supplied, Mapping) or 'store' in supplied or 'clock_us' in supplied:
                raise ValueError('extension dependencies may not override Studio state or clock')
            return {**supplied, 'store': store, 'clock_us': clock}
        def unavailable_authority_epoch() -> int:
            raise RuntimeError('no independent authority epoch provider configured')
        extensions: dict[str, object] = {}
        extensions['persona'] = persona_component(extension_deps('persona'))
        extensions['ownercontrol'] = ownercontrol_component(extension_deps('ownercontrol'))
        convergence_deps = extension_deps('convergence')
        convergence_deps.setdefault('authority_epoch', unavailable_authority_epoch)
        extensions['convergence'] = convergence_component(convergence_deps)
        extensions['capabilities'] = capabilities_component(extension_deps('capabilities'))
        bridge_deps = extension_deps('hr_bridge')
        bridge_deps.update({'trusted_issuer_ids': port('oversight_issuer_ids') or (),
                            'trusted_key_ids': port('oversight_key_ids') or ()})
        extensions['hr_bridge'] = hr_bridge_component(bridge_deps)
        extensions['creative_policy'] = creative_policy_component(extension_deps('creative_policy'))
        extensions['creative_impact'] = creative_impact_component(extension_deps('creative_impact'))
        extensions['creative_console'] = creative_console_component(extension_deps('creative_console'))
        extensions['creative_enforcement'] = creative_enforcement_component(extension_deps('creative_enforcement'))
        binding_deps = extension_deps('artifact_bindings')
        binding_deps.setdefault('registry', BindingRegistry(()))
        extensions['artifact_bindings'] = artifact_bindings_component(binding_deps)
        assert tuple(extensions) == EXTENSION_ORDER
        return StudioRuntime(store=store, components=comps, extensions=extensions)
    except BaseException:
        store.close()
        raise
