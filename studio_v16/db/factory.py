"""Server-owned backend selection. Never populate configuration from worker JSON."""
from dataclasses import dataclass
from pathlib import Path

from .storage_protocol import DurableStore


@dataclass(frozen=True)
class StorageConfig:
    backend: str = 'sqlite_v2'
    database: Path | None = None
    topology: str = 'local'
    busy_ms: int = 1000
    queue_limit: int = 64


def create_store(config: StorageConfig, *, postgres_connect=None) -> DurableStore:
    if not isinstance(config, StorageConfig):
        raise TypeError('trusted server StorageConfig required')
    if config.backend == 'sqlite_v2':
        from .sqlite.connection import ConnectionOwner
        from .sqlite.store import SQLiteStore
        if config.database is None:
            raise ValueError('server SQLite path required')
        owner = ConnectionOwner(config.database, topology=config.topology,
                                busy_ms=config.busy_ms, queue_limit=config.queue_limit)
        try:
            return SQLiteStore(owner)
        except BaseException:
            owner.close()
            raise
    if config.backend == 'shadow_legacy' and postgres_connect is not None:
        from .store import PostgresStore
        return PostgresStore(postgres_connect)
    raise ValueError('unsupported or unconfigured storage backend')
