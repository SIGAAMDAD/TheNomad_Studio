"""Production factory binds real C APSW state and refuses missing provider authority."""
from types import SimpleNamespace
from unittest.mock import Mock
import dataclasses
import pytest

apsw=pytest.importorskip("apsw")

from studio.api import FrozenContracts
from studio.app.errors import StudioError
from studio.app.production_factory import (TrustedPorts, ProjectProviderRouter,
                                          assemble_live_studio,build_from_environment)


def service(*methods):
    return SimpleNamespace(**{name:Mock() for name in methods})


def ports():
    return TrustedPorts(
        verifier=service("verify_bearer"),
        capability_probe=service("probe"),
        organization_policy=service("check_membership","check_tool_grants"),
        organization_source=service("get"),
        organization_trust=service("verify_package"),
        organization_host=service("tool_available","validator_passed"),
        production_authorization=service("require"),
        production_briefs=service("get_verified"),
        production_acceptance=service("require_accepted"),
        production_progress=service("require_stage"),
        production_stop=service("notify_task_canceled"),
        assurance_original=service("get","put_verified"),
        assurance_reviewers=service("choose"),
        assurance_validators=service("run"),
        report_profiles=service("load_verified"),
        report_blobs=service("store_verified"),
        report_relay=service("queue_report"),
        provider_for_project=lambda project:None,
        effect_readback=lambda intent:None,
        worker_authorize=lambda actor,project,op:None,
        verify_worker_receipt=lambda *_:False,
        release_evidence=service("verified_source_qa_ci","verified_canon_snapshot",
                                 "verified_artifacts","verified_release_digest",
                                 "approved_exact_effect"),
        release_publisher=service("observe_exact","publish"),
        integration_roster=SimpleNamespace(paths={},plan_sha256="digest"),
        integration_submissions=service("load_submission"),
        integration_qa=service("verify"),
        integration_remote=service("verify_exact"),
        verified_source_revision=lambda project,revision:None,
        startup_recovery=service("recover"))


def test_all_72_routes_are_physically_wired_to_one_apsw_writer(tmp_path):
    bundle=ports()
    app=assemble_live_studio(
        database_path=tmp_path/"studio.sqlite",
        blob_root=tmp_path/"blobs",source_root=tmp_path/"sources",
        contracts_root="CONTRACTS",ports=bundle,fresh=True)
    try:
        assert app.operator_service is not None
        assert app.operator_service.domains.missing(FrozenContracts("CONTRACTS"))==[]
        assert len(app.operator_service.contracts.operations)==72
        assert app.projects.store.writer is app.releases.state.writer
        assert app.projects.store.writer is app.recovery.state.writer
        assert app.projects.store.writer is app.integrations.state.writer
        assert any(component.name=="G-Actions-build-reconciliation"
                   for component in app.runtime.components)
        with app.runtime:
            assert app.runtime.ready
        assert not app.runtime.ready
        assert bundle.startup_recovery.recover.call_count==1
    finally:
        if app.runtime.ready:
            app.runtime.stop()


def test_factory_fails_closed_for_missing_or_wrong_project_services(tmp_path,monkeypatch):
    monkeypatch.delenv("STUDIO_TRUSTED_PORTS_FACTORY",raising=False)
    with pytest.raises(StudioError,match="TRUSTED_PORTS_FACTORY"):
        build_from_environment()
    broken=ports()
    broken.organization_policy.check_membership=None
    with pytest.raises(TypeError,match="organization_policy"):
        assemble_live_studio(
            database_path=tmp_path/"closed.sqlite",
            blob_root=tmp_path/"b",source_root=tmp_path/"s",
            contracts_root="CONTRACTS",ports=broken,fresh=True)
    router=ProjectProviderRouter(lambda project:SimpleNamespace(
        binding=SimpleNamespace(project_id="other")))
    with pytest.raises(StudioError,match="bound"):
        router.list_artifacts(project_id="project")


def _http(app, token, method, path, body=None):
    import io
    import json
    from wsgiref.util import setup_testing_defaults
    env={}
    setup_testing_defaults(env)
    encoded=json.dumps(body).encode() if body is not None else b''
    env.update(REQUEST_METHOD=method,PATH_INFO=path,QUERY_STRING='',
               CONTENT_LENGTH=str(len(encoded)),CONTENT_TYPE='application/json',
               HTTP_AUTHORIZATION='Bearer '+token,
               **{'wsgi.input':io.BytesIO(encoded)})
    recorded=[]
    response=b''.join(app(env,lambda status,headers:recorded.append(status)))
    return int(recorded[0][:3]),json.loads(response)


def test_live_wsgi_project_governance_and_persistent_worker_lifecycle(tmp_path):
    import hashlib
    from studio.api import (ConfiguredOpaqueVerifier, StudioWSGI, VerifiedPrincipal)
    config=ports()
    token='owner.'+'z'*60
    config=dataclasses.replace(config,verifier=ConfiguredOpaqueVerifier({
        hashlib.sha256(token.encode()).hexdigest():
            VerifiedPrincipal('owner','trusted','owner','proof',True)}),
        bootstrap_credentials=(token,))
    app=assemble_live_studio(
        database_path=tmp_path/'studio.sqlite',
        blob_root=tmp_path/'blobs',source_root=tmp_path/'sources',
        contracts_root='CONTRACTS',ports=config,fresh=True)
    service=StudioWSGI(app.operator_service)
    writer=app.projects.store.writer
    try:
        # Bootstrap has no existing project grant; the persisted owner grant
        # is created in the same C transaction and used on the next request.
        status, created=_http(service,token,'POST','/v1/projects',{
            'request_id':'project-create','name':'Nomad','charter_digest':'a'*64})
        assert status==200
        project=created['project_id']
        assert _http(service,token,'POST','/v1/projects',{
            'request_id':'project-create','name':'Nomad','charter_digest':'a'*64})==(status,created)
        status, bible=_http(service,token,'POST',f'/v1/projects/{project}/bibles',{
            'request_id':'bible1','title':'Design','kind':'design'})
        assert status==200 and bible['bible_id']
        assert _http(service,token,'GET',f'/v1/projects/{project}/bibles')==(
            200,{'bibles':[{'bible_id':bible['bible_id'],
                             'kind':'design','active_edition_id':''}]})
        writer._connection.execute(
            "INSERT INTO specialist_roles(role_id,role_revision,role_manifest_digest,state) "
            "VALUES('coder','1','digest','ready')")
        status, registered=_http(service,token,'POST',f'/v1/projects/{project}/workers',{
            'request_id':'worker1','worker_id':'coder-1',
            'browser_chat_ref':'manual-browser','role_id':'coder'})
        assert status==200 and registered['state']=='registered'
        status, shift=_http(service,token,'POST',
            f'/v1/projects/{project}/workers/coder-1/shifts',{
                'request_id':'shift-start','shift_id':'shift1'})
        assert status==200 and shift['state']=='awaiting_hello'
        assert writer.read(project,lambda tx:tx.one(
            'SELECT root_worker_id FROM worker_lineages WHERE project_id=? AND worker_id=?',
            (project,'coder-1'))) == {'root_worker_id':'coder-1'}
        assert writer.read(project,lambda tx:tx.one(
            'SELECT state FROM worker_shifts WHERE project_id=? AND shift_id=?',
            (project,'shift1'))) == {'state':'awaiting_hello'}
        assert _http(service,token,'GET',
                     '/v1/projects/other/workers')[0]==403
    finally:
        writer.close()
