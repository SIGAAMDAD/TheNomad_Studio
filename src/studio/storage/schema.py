"""Fresh-database-only frozen schema installer; no historical migrations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import Conflict, Invalid

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schema"


def schema_bytes() -> bytes:
    path = SCHEMA_DIR / "initial_schema.sql"
    data = path.read_bytes()
    expected = (SCHEMA_DIR / "schema.sha256").read_text().split()[0]
    if hashlib.sha256(data).hexdigest() != expected:
        raise Invalid("frozen initial schema SHA-256 differs from packaged digest")
    return data


def table_owners() -> dict[str, str]:
    return json.loads((SCHEMA_DIR / "table_owners.json").read_text(encoding="utf-8"))


def create_new_database(connection: Any) -> None:
    """Only against an empty SQLite database. Caller owns the single-writer lifecycle."""
    cursor = connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view','trigger','index') AND name NOT LIKE 'sqlite_%' LIMIT 1")
    if cursor.fetchone() is not None:
        raise Conflict("database is not empty; migrations are forbidden")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    # APSW connection.execute supports multiple statements; the final schema is unchanged.
    # Drain APSW cursor: a multi-statement script may execute lazily.
    for _ in connection.execute(schema_bytes().decode("utf-8")):
        pass
    installed = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if installed != set(table_owners()):
        raise Invalid("frozen initial schema did not install all tables")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise Invalid("foreign_keys not enabled")
    check = connection.execute("PRAGMA integrity_check").fetchone()
    if check != ("ok",):
        raise Invalid("new database failed integrity_check")


def ensure_existing_database(connection: Any) -> None:
    expected = set(table_owners())
    got = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if got != expected:
        raise Invalid(f"wrong database schema: missing={sorted(expected-got)}, extra={sorted(got-expected)}")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise Invalid("foreign_keys disabled")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise Invalid("existing database failed quick_check")
    # The table list alone cannot detect removed triggers or altered SQL.
    # Build an expected schema with the SAME APSW SQLite engine, then compare
    # exact sqlite_master definitions (including indexes and triggers).
    try:
        import apsw
    except ImportError as exc:
        from .errors import Unavailable
        raise Unavailable("APSW required to verify frozen schema") from exc
    reference = apsw.Connection(":memory:")
    try:
        create_new_database(reference)
        query = "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY type,name"
        if list(connection.execute(query)) != list(reference.execute(query)):
            raise Invalid("existing database schema/triggers/indexes differ from frozen DDL")
    finally:
        reference.close()
