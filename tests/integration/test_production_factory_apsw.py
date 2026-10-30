"""Production factory binds real C APSW state and refuses missing provider authority."""
from types import SimpleNamespace
from unittest.mock import Mock
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
