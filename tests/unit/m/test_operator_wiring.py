"""Composition binds 72 real-shaped handlers and refuses missing runtime services."""
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from studio.api import FrozenContracts, EventPageService, VerifiedPrincipal
from studio.api.errors import ApiError
from studio.agents.operations import WorkerOperations
from studio.app.composition import StudioComposition
from studio.app.governance_commands import GovernanceCommands
from studio.app.organization_commands import OrganizationCommands
from studio.app.production_commands import ProductionCommands
from studio.app.assurance_commands import AssuranceCommands
from studio.app.operations_commands import OperationsCommands
from studio.app.provider_commands import ProviderCommands
from studio.app.operator_wiring import assemble_operator_service


def _names(bridge):
    return SimpleNamespace(**{name:Mock() for name in bridge.operation_ids})


def _fixture():
    m=StudioComposition(projects=object(),releases=object(),recovery=object(),runtime=object())
    a=GovernanceCommands(_names(GovernanceCommands))
    b=OrganizationCommands(_names(OrganizationCommands))
    d=ProductionCommands(_names(ProductionCommands))
    e=AssuranceCommands(_names(AssuranceCommands))
    f=OperationsCommands(SimpleNamespace(**{
        name:Mock() for name in ('configure_budget','get_budgets','list_reports',
                                 'list_notifications','ack_notification')}),
        profiles=SimpleNamespace(load_verified=Mock()),
        blobs=SimpleNamespace(store_verified=Mock()))
    g=ProviderCommands(SimpleNamespace(**{
        name:Mock() for name in ('list_artifacts','get_artifact','attach_connector',
                                 'run_build','reconcile_effect','observe_effect')}),
        effect_readback=lambda intent:None)
    worker=WorkerOperations(SimpleNamespace(),verify_receipt=lambda *_:False)
    events=EventPageService(SimpleNamespace(scan_project_events=Mock(return_value=[])))
    verifier=SimpleNamespace(verify_bearer=Mock(return_value=VerifiedPrincipal(
        'human','issuer','subject','proof',True)))
    grants=SimpleNamespace(granted_project_ids=Mock(return_value={'p1'}))
    transactions=SimpleNamespace(execute_idempotent=Mock())
    return dict(composition=m,governance=a,organization=b,production=d,
                assurance=e,operations=f,providers=g,workers=worker,
                events=events,verifier=verifier,grants=grants,
                transactions=transactions,contracts_root='CONTRACTS')


def test_full_catalog_is_registered_once_with_frozen_owner_routing():
    args=_fixture()
    composed=assemble_operator_service(**args)
    registry=composed.operator_service.domains
    contracts=FrozenContracts('CONTRACTS')
    assert registry.missing(contracts)==[]
    assert len(contracts.operations)==72
    for op in contracts.operations.values():
        assert callable(registry.get(op))
    assert composed.operator_service.transactions is args['transactions']
    assert composed.operator_service.verifier is args['verifier']
    assert composed.operator_service.grants is args['grants']
    assert composed.operator_service.contracts is not None


def test_missing_real_domain_or_auth_port_refuses_assembly():
    args=_fixture()
    args['providers']=object()
    with pytest.raises(TypeError,match='owner G'):
        assemble_operator_service(**args)
    args=_fixture()
    args['transactions']=None
    with pytest.raises(TypeError,match='durable mutation runner'):
        assemble_operator_service(**args)
    args=_fixture()
    args['governance'].service.createBible=None
    with pytest.raises(ApiError,match='missing implementation methods'):
        assemble_operator_service(**args)


def test_double_installation_is_rejected():
    args=_fixture()
    good=assemble_operator_service(**args)
    args['composition']=good
    with pytest.raises(ValueError,match='already attached'):
        assemble_operator_service(**args)
