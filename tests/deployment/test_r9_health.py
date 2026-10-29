from nomad_studio.deployment.config import DeploymentConfig
from nomad_studio.deployment.health import check_host

def test_missing_provider_and_service_monitor_never_healthy():
    records=check_host(DeploymentConfig(),check_files=False)
    assert {r.name:r.status for r in records}=={'service':'NOT_VERIFIED','dns':'NOT_VERIFIED'}

def test_failing_dns_refuses_readiness():
    config=DeploymentConfig(external_url='https://studio.example.org')
    def failed(*_,**__):raise OSError('offline')
    records=check_host(config,dns_lookup=failed,service_alive=lambda:True,check_files=False)
    assert {r.name:r.status for r in records}=={'service':'OK','dns':'BLOCKED_LOCAL'}
