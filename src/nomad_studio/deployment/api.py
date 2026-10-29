"""CAB-H fail-closed R9 local readiness projection for P2 runtime."""
from __future__ import annotations
from typing import Any, Callable, Sequence
from nomad_studio.trust.port_types import Result
from .config import DeploymentConfig
from .health import HealthCheck,check_host

class DeploymentReadiness:
    def __init__(self, config: DeploymentConfig, host: Any, required_capabilities: Sequence[str],
                 *, infrastructure_checks: Callable[[], Sequence[HealthCheck]] | None = None):
        self.config,self.host,self.required_capabilities=config,host,tuple(required_capabilities)
        self.infrastructure_checks=infrastructure_checks or (lambda:check_host(config))

    def check(self, provider_id: str) -> Result:
        if self.config.missing_settings():
            return Result('BLOCKED_LOCAL','RELEASE_BLOCKED',
                record={'missing_settings':tuple(self.config.missing_settings()),'ready':False})
        try:
            checks=tuple(self.infrastructure_checks())
        except Exception:
            return Result('NOT_VERIFIED','TEST_NOT_RUN',record={'ready':False})
        if not checks or any(not isinstance(c,HealthCheck) for c in checks):
            return Result('NOT_VERIFIED','TEST_NOT_RUN',record={'ready':False})
        failed={c.name:c.status for c in checks if c.status!='OK'}
        if failed:
            return Result('BLOCKED_LOCAL' if any(x=='BLOCKED_LOCAL' for x in failed.values())
                          else 'NOT_VERIFIED',
                          'RELEASE_BLOCKED' if any(x=='BLOCKED_LOCAL' for x in failed.values())
                          else 'TEST_NOT_RUN',record={'ready':False,'failed_infrastructure':failed})
        manifest=self.host.get_capability_manifest(provider_id)
        if manifest.status != 'OK' or not manifest.record or manifest.receipt_ref is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED',record={'ready':False})
        caps=manifest.record.get('capabilities',{})
        missing={k:caps.get(k,'NOT_VERIFIED') for k in self.required_capabilities
                 if caps.get(k) != 'SUPPORTED'}
        if missing:
            status='UNSUPPORTED' if any(v=='UNSUPPORTED' for v in missing.values()) else 'NOT_VERIFIED'
            return Result(status,'PROVIDER_UNSUPPORTED' if status=='UNSUPPORTED' else 'PROVIDER_NOT_VERIFIED',
                          record={'ready':False,'missing_capabilities':missing})
        return Result('OK','OK',record={'ready':True},receipt_ref=manifest.receipt_ref,
                      evidence_refs=manifest.evidence_refs)
