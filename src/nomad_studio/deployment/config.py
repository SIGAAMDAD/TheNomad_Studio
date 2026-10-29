"""Fail-closed deployment configuration and security-oriented filesystem checks.

Credentials are supplied as paths to files mounted by the operator, never values.
This module does not create a network listener or obtain privileged authority.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Mapping
import ipaddress
import stat
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    gateway_host: str = '127.0.0.1'
    gateway_port: int = 8765
    external_url: str = ''
    connector_mode: str = 'NOT_CONFIGURED'
    sqlite_path: str = '/var/lib/nomad-studio/state/studio.sqlite3'
    secrets_dir: str = '/etc/nomad-studio/keys'
    tls_ca_file: str = ''
    tls_cert_file: str = ''
    tls_key_file: str = ''
    github_app_key_file: str = ''
    drive_credentials_file: str = ''
    launch_signing_key_file: str = ''
    provider_bridge_name: str = ''

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None) -> 'DeploymentConfig':
        if values is None:
            values = {}
        if not isinstance(values, Mapping):
            raise ConfigurationError('INVALID_SCHEMA', 'configuration must be a mapping')
        names = {f.name for f in fields(cls)}
        if set(values) - names:
            raise ConfigurationError('INVALID_SCHEMA', 'unknown configuration field')
        for k, v in values.items():
            if k == 'gateway_port':
                if type(v) is not int or not 1 <= v <= 65535:
                    raise ConfigurationError('INVALID_SCHEMA', 'gateway_port must be an integer port')
            elif not isinstance(v, str):
                raise ConfigurationError('INVALID_SCHEMA', 'configuration fields must be strings')
        cfg = cls(**values)
        cfg.validate_static()
        return cfg

    def validate_static(self) -> None:
        # Gateway is ALWAYS private; the TLS/mTLS reverse proxy is the only
        # externally reachable front door. DNS names and 0.0.0.0 are denied.
        try:
            host = ipaddress.ip_address(self.gateway_host)
        except ValueError as exc:
            raise ConfigurationError('INVALID_SCHEMA', 'gateway_host must be an IP loopback address') from exc
        if not host.is_loopback:
            raise ConfigurationError('INVALID_SCOPE', 'public or non-loopback gateway listener rejected')
        if self.connector_mode not in {'NOT_CONFIGURED', 'SUPPORTED_TOOL_BRIDGE', 'SIMULATED'}:
            raise ConfigurationError('INVALID_SCHEMA', 'unknown connector_mode')
        if self.external_url:
            if self.external_url.strip() != self.external_url or any(ord(c) < 32 for c in self.external_url):
                raise ConfigurationError('INVALID_SCHEMA', 'invalid HTTPS origin characters')
            try:
                url = urlsplit(self.external_url)
            except ValueError as exc:
                raise ConfigurationError('INVALID_SCHEMA', 'invalid HTTPS origin') from exc
            if (url.scheme != 'https' or not url.hostname or url.username or url.password
                    or url.query or url.fragment or url.path not in {'', '/'}):
                raise ConfigurationError('INVALID_SCHEMA', 'external_url must be an HTTPS origin without userinfo')
            try:
                port = url.port
            except ValueError as exc:
                raise ConfigurationError('INVALID_SCHEMA', 'invalid HTTPS port') from exc
            if port is not None and not 1 <= port <= 65535:
                raise ConfigurationError('INVALID_SCHEMA', 'invalid HTTPS port')
            if url.hostname.lower() in {'localhost', 'localhost.localdomain'}:
                raise ConfigurationError('INVALID_SCOPE', 'connector route cannot use localhost')
            try:
                address = ipaddress.ip_address(url.hostname)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    raise ConfigurationError('INVALID_SCOPE', 'connector route requires a public HTTPS origin')
        for field_name in ('sqlite_path', 'secrets_dir', 'tls_ca_file', 'tls_cert_file',
                           'tls_key_file', 'github_app_key_file', 'drive_credentials_file',
                           'launch_signing_key_file'):
            value = getattr(self, field_name)
            if value and (not Path(value).is_absolute() or '\x00' in value):
                raise ConfigurationError('INVALID_SCHEMA', f'{field_name} must be an absolute path')

    def missing_settings(self) -> list[str]:
        required = ('external_url', 'tls_ca_file', 'tls_cert_file', 'tls_key_file',
                    'github_app_key_file', 'drive_credentials_file',
                    'launch_signing_key_file', 'provider_bridge_name')
        missing = [key for key in required if not getattr(self, key)]
        if self.connector_mode != 'SUPPORTED_TOOL_BRIDGE':
            missing.append('supported_real_connector')
        return missing


def secure_file(path: str, *, root: str | None = None, require_private: bool = True) -> str | None:
    """Return a safe, secret-free failure code, never file bytes or paths."""
    try:
        p = Path(path)
        if not p.is_absolute():
            return 'INVALID_PATH'
        if root:
            base = Path(root).resolve(strict=True)
            # Reject symlink traversal through file name or parent directories.
            if not p.resolve(strict=True).is_relative_to(base):
                return 'INVALID_PATH'
        # Reject symlinks in EACH path component, not just final file.
        current = Path('/')
        for part in p.parts[1:]:
            current = current / part
            if stat.S_ISLNK(current.lstat().st_mode):
                return 'INSECURE_SYMLINK'
        mode = p.stat()
        if not stat.S_ISREG(mode.st_mode):
            return 'NOT_REGULAR_FILE'
        if mode.st_size == 0:
            return 'EMPTY_FILE'
        if require_private and (stat.S_IMODE(mode.st_mode) & 0o077):
            return 'INSECURE_PERMISSIONS'
    except (FileNotFoundError, NotADirectoryError):
        return 'NOT_CONFIGURED'
    except (PermissionError, OSError):
        return 'HOST_IO_ERROR'
    return None


def private_dir(path: str) -> str | None:
    try:
        p = Path(path)
        if not p.is_absolute():
            return 'INVALID_PATH'
        current = Path('/')
        for part in p.parts[1:]:
            current = current / part
            if stat.S_ISLNK(current.lstat().st_mode):
                return 'INSECURE_SYMLINK'
        mode = p.stat()
        if not stat.S_ISDIR(mode.st_mode):
            return 'NOT_DIRECTORY'
        if stat.S_IMODE(mode.st_mode) & 0o077:
            return 'INSECURE_PERMISSIONS'
    except FileNotFoundError:
        return 'NOT_CONFIGURED'
    except (PermissionError, OSError):
        return 'HOST_IO_ERROR'
    return None
