"""M factory rejects missing real provider capabilities and unsafe bootstrap config."""
from dataclasses import fields, replace
from types import SimpleNamespace, ModuleType
import sys
import pytest

import studio.app.production_factory as factory
from studio.app.errors import StudioError


def ports():
    needed = {
        'verifier':['verify_bearer'], 'capability_probe':['probe'],
        'organization_policy':['check_membership','check_tool_grants'],
        'organization_source':['get'], 'organization_trust':['verify_package'],
        'organization_host':['tool_available','validator_passed'],
        'production_authorization':['require'], 'production_briefs':['get_verified'],
        'production_acceptance':['require_accepted'], 'production_progress':['require_stage'],
        'production_stop':['notify_task_canceled'], 'assurance_original':['get','put_verified'],
        'assurance_reviewers':['choose'], 'assurance_validators':['run'],
        'report_profiles':['load_verified'], 'report_blobs':['store_verified'],
        'report_relay':['queue_report'],
        'release_evidence':['verified_source_qa_ci','verified_canon_snapshot','verified_artifacts',
                            'verified_release_digest','approved_exact_effect'],
        'release_publisher':['observe_exact','publish'],
        'integration_submissions':['load_submission'], 'integration_qa':['verify'],
        'integration_remote':['verify_exact'], 'startup_recovery':['recover'],
    }
    def dummy(*args,**kwargs):return None
    result={f.name:None for f in fields(factory.TrustedPorts)}
    for name, methods in needed.items():
        result[name]=SimpleNamespace(**{method:dummy for method in methods})
    for name in ('provider_for_project','effect_readback','worker_authorize',
                 'verify_worker_receipt','verified_source_revision'):
        result[name]=dummy
    result['integration_roster']=SimpleNamespace(paths={'wg':['src/wg/**']},plan_sha256='a'*64)
    return factory.TrustedPorts(**result)


def test_valid_trusted_port_contract_accepts_installed_methods():
    assert factory._validate_external(ports()) is None


@pytest.mark.parametrize('field,missing',[
    ('verifier','verify_bearer'),('organization_policy','check_tool_grants'),
    ('production_briefs','get_verified'),('release_publisher','observe_exact'),
    ('assurance_reviewers','choose'),('startup_recovery','recover')
])
def test_required_external_capability_cannot_be_inferred(field,missing):
    p=ports()
    broken=SimpleNamespace(**{key:value for key,value in vars(getattr(p,field)).items() if key!=missing})
    with pytest.raises(TypeError,match=field):
        factory._validate_external(replace(p,**{field:broken}))


@pytest.mark.parametrize('field',[
    'provider_for_project','effect_readback','worker_authorize',
    'verify_worker_receipt','verified_source_revision',
])
def test_mutating_ports_must_be_callable(field):
    with pytest.raises(TypeError,match=field):
        factory._validate_external(replace(ports(),**{field:object()}))


def test_optional_command_and_webhook_ports_are_explicitly_validated():
    p=ports()
    with pytest.raises(TypeError,match='outbox'):
        factory._validate_external(replace(p,command_delivery=object()))
    with pytest.raises(TypeError,match='webhook'):
        factory._validate_external(replace(p,github_webhook_hints=object()))
    with pytest.raises(TypeError,match='roster'):
        factory._validate_external(replace(p,integration_roster=object()))
    assert factory._validate_external(replace(p,command_delivery=SimpleNamespace(start=lambda:None,stop=lambda:None))) is None


def test_non_trusted_provider_object_is_rejected():
    with pytest.raises(TypeError,match='TrustedPorts'):
        factory._validate_external(SimpleNamespace())


def test_build_environment_missing_factory_fail_closed(monkeypatch):
    monkeypatch.delenv('STUDIO_TRUSTED_PORTS_FACTORY',raising=False)
    with pytest.raises(StudioError,match='not configured'):
        factory.build_from_environment()
    monkeypatch.setenv('STUDIO_TRUSTED_PORTS_FACTORY','invalid.module:bad path')
    with pytest.raises(StudioError,match='invalid'):
        factory.build_from_environment()


def test_build_environment_validates_required_storage_and_outbox(monkeypatch,tmp_path):
    module=ModuleType('studio_r5r_test_trusted_ports')
    module.get_ports=ports
    monkeypatch.setitem(sys.modules,module.__name__,module)
    monkeypatch.setenv('STUDIO_TRUSTED_PORTS_FACTORY',module.__name__+':get_ports')
    with pytest.raises(StudioError,match='publisher'):
        factory.build_from_environment()
    module.get_ports=lambda:replace(ports(),command_delivery=SimpleNamespace(start=lambda:None,stop=lambda:None))
    with pytest.raises(StudioError,match='source target'):
        factory.build_from_environment()
    module.get_ports=lambda:replace(ports(),command_delivery=SimpleNamespace(start=lambda:None,stop=lambda:None),
                                    source_target_for_project=lambda project: None)
    with pytest.raises(StudioError,match='durable database'):
        factory.build_from_environment()
    monkeypatch.setenv('STUDIO_APSW_DATABASE',str(tmp_path/'studio.sqlite'))
    monkeypatch.setenv('STUDIO_BLOB_ROOT',str(tmp_path/'blobs'))
    monkeypatch.setenv('STUDIO_SOURCE_ROOT',str(tmp_path/'sources'))
    seen=[]
    def fake_assemble(**kwargs):
        seen.append(kwargs)
        return 'composed'
    monkeypatch.setattr(factory,'assemble_live_studio',fake_assemble)
    assert factory.build_from_environment() == 'composed'
    assert seen[0]['database_path'].name=='studio.sqlite'
    assert seen[0]['ports'].command_delivery is not None