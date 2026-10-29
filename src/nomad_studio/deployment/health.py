"""R9 local infrastructure checks. Local checks never claim provider verification."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import socket
from .config import DeploymentConfig, secure_file, private_dir

@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    status: str
    code: str

def check_host(config: DeploymentConfig, *, dns_lookup: Callable[..., object] = socket.getaddrinfo,
               service_alive: Callable[[], bool] | None = None,
               check_files: bool = True) -> list[HealthCheck]:
    checks=[]
    if service_alive is None:
        checks.append(HealthCheck('service','NOT_VERIFIED','TEST_NOT_RUN'))
    else:
        try:
            alive=service_alive()
            checks.append(HealthCheck('service','OK' if alive else 'BLOCKED_LOCAL',
                                      'OK' if alive else 'RELEASE_BLOCKED'))
        except Exception:
            checks.append(HealthCheck('service','NOT_VERIFIED','TEST_NOT_RUN'))
    if not config.external_url:
        checks.append(HealthCheck('dns','NOT_VERIFIED','PROVIDER_NOT_VERIFIED'))
    else:
        from urllib.parse import urlsplit
        hostname=urlsplit(config.external_url).hostname
        try:
            resolved=bool(dns_lookup(hostname,443,type=socket.SOCK_STREAM))
            checks.append(HealthCheck('dns','OK' if resolved else 'BLOCKED_LOCAL',
                                      'OK' if resolved else 'RELEASE_BLOCKED'))
        except (OSError,ValueError):
            checks.append(HealthCheck('dns','BLOCKED_LOCAL','RELEASE_BLOCKED'))
    if check_files:
        paths={'sqlite_parent':private_dir(str(Path(config.sqlite_path).parent)),
               'secrets_dir':private_dir(config.secrets_dir)}
        for key in ('tls_ca_file','tls_cert_file','tls_key_file','github_app_key_file',
                    'drive_credentials_file','launch_signing_key_file'):
            value=getattr(config,key)
            paths[key]=secure_file(value,require_private=key not in {'tls_ca_file','tls_cert_file'}) if value else 'NOT_CONFIGURED'
        for name,error in paths.items():
            checks.append(HealthCheck(name,'OK' if error is None else 'NOT_VERIFIED'
                                      if error=='NOT_CONFIGURED' else 'BLOCKED_LOCAL',
                                      'OK' if error is None else 'PROVIDER_NOT_VERIFIED'
                                      if error=='NOT_CONFIGURED' else 'RELEASE_BLOCKED'))
    return checks
