"""A05: runnable SQLite/mTLS SHADOW service; privileged effects off by default.

All authority/QA/owner evidence comes from an independent server-side reader.
No worker input may enable publication, install credentials or select a database.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import threading
from pathlib import Path

from studio_v16.adapters.authority import DurableAuthority
from studio_v16.db.factory import StorageConfig, create_store
from studio_v16.gateway.gateway import Gateway
from studio_v16.gateway.source_verification import SourceBaseVerifier, SourceRoute
from studio_v16.gateway.transport import MTLSIdentity, server_context, serve
from studio_v16.providers.git_provider import IsolatedGitProvider


class FileAudit:
    """Private append-only, synchronized and fsync'd denial/receipt journal."""
    def __init__(self, path: Path):
        if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('audit journal requires absolute nonsymlink path')
        parent = path.parent
        if parent.stat().st_uid != os.getuid() or stat.S_IMODE(parent.stat().st_mode) & 0o077:
            raise ValueError('private audit directory required')
        self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self._lock = threading.Lock()

    def emit(self, record):
        raw = (json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n').encode()
        if len(raw) > 65536:
            raise ValueError('audit event too large')
        with self._lock:
            if os.write(self._fd, raw) != len(raw):
                raise OSError('AUDIT_SHORT_WRITE')
            os.fsync(self._fd)

    def close(self):
        os.close(self._fd)


class SQLiteService:
    def __init__(self, config: dict, *, evidence_loader=None):
        expected = {'database', 'audit_path', 'tls_cert', 'tls_key', 'tls_ca', 'client_fingerprints',
                    'repository_remotes', 'source_routes', 'host', 'port'}
        if set(config) != expected:
            raise ValueError('strict service configuration required')
        if config['host'] != '127.0.0.1' or type(config['port']) is not int or not 1 <= config['port'] <= 65535:
            raise ValueError('loopback-only listener required')
        self._tls_files = tuple(config[n] for n in ('tls_cert', 'tls_key', 'tls_ca'))
        if any(not isinstance(f, str) or not Path(f).is_absolute() for f in self._tls_files):
            raise ValueError('absolute mTLS file paths required')
        self.host, self.port = config['host'], config['port']
        self._evidence_configured = evidence_loader is not None
        self.identities = MTLSIdentity(config['client_fingerprints'])
        self.audit = None
        self.store = None
        try:
            self.audit = FileAudit(Path(config['audit_path']))
            remotes = config['repository_remotes']
            self.git = IsolatedGitProvider(remotes, enable_external_effects=False) if remotes else None
            routes = {x['job_id']: SourceRoute(**x) for x in config['source_routes']}
            self.source_verifier = SourceBaseVerifier(self.git, routes) if routes else None
            if routes and not self.git:
                raise ValueError('source routes require isolated Git provider')
            self.store = create_store(StorageConfig(database=Path(config['database'])),
                                      source_verifier=self.source_verifier)
            self.authority = DurableAuthority(self.store, authoritative_evidence=evidence_loader)
            self.gateway = Gateway(authority=self.authority, git=self.git, audit=self.audit)
        except BaseException:
            self.close()
            raise

    def health(self):
        with self.store.owner.connection(readonly=True) as conn:
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            migrations = conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0]
        return {'schema': 'NOMAD-SERVICE-HEALTH/1', 'mode': 'SHADOW',
                'database_integrity': integrity, 'schema_migration_count': migrations,
                'external_effects_enabled': False, 'independent_evidence_loader_configured': self._evidence_configured}

    def run(self):
        tls = server_context(certfile=self._tls_files[0], keyfile=self._tls_files[1],
                             cafile=self._tls_files[2])
        serve(self.gateway, self.identities, tls, host=self.host, port=self.port)

    def close(self):
        if self.store is not None:
            self.store.close()
            self.store = None
        if self.audit is not None:
            self.audit.close()
            self.audit = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not args.config.is_absolute() or args.config.is_symlink():
        raise ValueError('absolute private config file required')
    data = json.loads(args.config.read_text(encoding='utf-8'))
    with SQLiteService(data) as service:
        if args.check:
            print(json.dumps(service.health(), sort_keys=True))
        else:
            service.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
