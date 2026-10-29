"""The Nomad Studio V1 only: fresh-schema initialization, no upgrade path.

No development-era migration registry, source-history replay, or old-database
conversion is accepted. Persistence is safe for restarts of this exact V1 schema.
"""
from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import sqlite3

from .errors import ConfigurationError

SCHEMA_VERSION = 1
SCHEMA_SHA256 = '380e7caf3bd88c53a077da5aa68c818e19e38c78f0545bfb77765fe703eceb92'
SCHEMA_NAMESPACES = frozenset(['archive', 'artifacts', 'budget', 'console', 'knowledge', 'operations', 'organization', 'oversight', 'publication', 'relay', 'review', 'scheduler', 'shift', 'source', 'state', 'trust', 'hr_bridge', 'persona', 'ownercontrol', 'convergence', 'capabilities', 'creative_policy'])


def _sql() -> str:
    try:
        value = files('nomad_studio.state').joinpath('schema_v1.sql').read_text('utf-8')
    except (OSError, FileNotFoundError) as exc:
        raise ConfigurationError('Studio V1 initial schema is missing') from exc
    if sha256(value.encode('utf-8')).hexdigest() != SCHEMA_SHA256:
        raise ConfigurationError('Studio V1 initial schema digest mismatch')
    return value


def initialize_v1_schema(connection: sqlite3.Connection) -> None:
    """Install once on an empty SQLite DB; refuse any historical DB format."""
    sql = _sql()  # verify even if an existing database appears initialized
    existing = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view','trigger') AND name NOT LIKE 'sqlite_%'")}
    if not existing:
        # An executable, one-shot bootstrap. A partial or invalid schema is rolled back.
        # Connection.executescript itself commits; include the explicit transaction.
        try:
            connection.executescript('BEGIN IMMEDIATE;\n' + sql + """
CREATE TABLE studio_v1_schema (
  installation INTEGER PRIMARY KEY CHECK (installation=1),
  version INTEGER NOT NULL CHECK (version=1),
  sha256 TEXT NOT NULL
);
INSERT INTO studio_v1_schema(installation,version,sha256)
VALUES (1,1,'380e7caf3bd88c53a077da5aa68c818e19e38c78f0545bfb77765fe703eceb92');
PRAGMA user_version = 1;
COMMIT;
""")
        except sqlite3.DatabaseError:
            if connection.in_transaction:
                connection.rollback()
            raise
        return
    if 'studio_v1_schema' not in existing or 'state_migrations' in existing:
        raise ConfigurationError('database is not a clean Studio V1 baseline; no historical upgrade path')
    identity = connection.execute('SELECT version,sha256 FROM studio_v1_schema WHERE installation=1').fetchone()
    if identity is None or identity[0] != SCHEMA_VERSION or identity[1] != SCHEMA_SHA256:
        raise ConfigurationError('Studio V1 schema identity mismatch')
    if connection.execute('PRAGMA user_version').fetchone()[0] != 1:
        raise ConfigurationError('Studio V1 database version mismatch')
    if 'state_events' not in existing or 'state_records' not in existing:
        raise ConfigurationError('Studio V1 database is missing authority tables')
