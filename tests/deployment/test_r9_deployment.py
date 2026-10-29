from nomad_studio.deployment.api import DeploymentReadiness
from nomad_studio.deployment.config import DeploymentConfig
from nomad_studio.deployment.health import HealthCheck
from nomad_studio.trust.port_types import Result

class NativeHost:
    def __init__(self,status,capabilities):self.status=status;self.capabilities=capabilities
    def get_capability_manifest(self,provider):
        return Result(self.status,'OK' if self.status=='OK' else 'PROVIDER_NOT_VERIFIED',
                     record={'capabilities':self.capabilities},
                     receipt_ref='native/manifest' if self.status=='OK' else None)

def config():
    return DeploymentConfig(external_url='https://studio.example.org',connector_mode='SUPPORTED_TOOL_BRIDGE',
        provider_bridge_name='host-native',tls_ca_file='/etc/nomad/ca.pem',
        tls_cert_file='/etc/nomad/server.pem',tls_key_file='/etc/nomad/key.pem',
        github_app_key_file='/etc/nomad/github.pem',drive_credentials_file='/etc/nomad/drive.pem',
        launch_signing_key_file='/etc/nomad/owner.pem')

def healthy():return [HealthCheck('service','OK','OK'),HealthCheck('dns','OK','OK')]

def test_unconfigured_never_ready():
    assert DeploymentReadiness(DeploymentConfig(),NativeHost('OK',{'dispatch':'SUPPORTED'}),
                               ['dispatch']).check('provider/1').status=='BLOCKED_LOCAL'

def test_observed_unsupported_is_explicit():
    check=DeploymentReadiness(config(),NativeHost('OK',{'dispatch':'UNSUPPORTED'}),['dispatch'],infrastructure_checks=healthy)
    result=check.check('provider/1')
    assert result.status=='UNSUPPORTED' and result.record['ready'] is False

def test_manifest_readback_required_for_readiness():
    check=DeploymentReadiness(config(),NativeHost('NOT_VERIFIED',{'dispatch':'SUPPORTED'}),['dispatch'],infrastructure_checks=healthy)
    assert check.check('provider/1').status=='NOT_VERIFIED'


def test_live_manifest_cannot_overrule_missing_local_service_or_files():
    check=DeploymentReadiness(config(),NativeHost('OK',{'dispatch':'SUPPORTED'}),['dispatch'])
    assert check.check('provider/1').status!='OK'
