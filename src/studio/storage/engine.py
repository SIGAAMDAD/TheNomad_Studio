"""APSW-only, single-writer, project/domain-scoped named SQL execution.

Domain bindings are minted by trusted application composition, never handed to
browser-authored relay messages. ActorContext is supplied by WG-J's trusted
identity boundary; this module checks scope but does not authenticate users.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Sequence

from .errors import Conflict, Forbidden, Invalid, Missing, Unavailable
from .registry import GLOBAL_CATALOG, GLOBAL_RESTRICTED, NamedRegistry, TablePlan, assert_project_value, bounded_limit, safe_keys, safe_pk_cursor
from .schema import create_new_database, ensure_existing_database
from .states import StateMachines


@dataclass(frozen=True)
class DomainBinding:
    name: str
    _key: object


@dataclass(frozen=True)
class Actor:
    principal_id: str
    issuer: str
    allowed_project_ids: tuple[str, ...]
    trusted_proof_ref: str

    @staticmethod
    def from_context(value: Mapping[str, Any]) -> "Actor":
        required = ("principal_id", "issuer", "allowed_project_ids", "trusted_proof_ref")
        if not isinstance(value, Mapping) or any(k not in value for k in required):
            raise Forbidden("trusted actor context required")
        if not all(isinstance(value[k], str) and value[k] for k in ("principal_id", "issuer", "trusted_proof_ref")):
            raise Forbidden("incomplete authenticated actor context")
        projects = value["allowed_project_ids"]
        if not isinstance(projects, (list, tuple)) or any(not isinstance(p, str) or not p for p in projects):
            raise Forbidden("invalid actor project grants")
        return Actor(value["principal_id"], value["issuer"], tuple(projects), value["trusted_proof_ref"])


class ScopedStore:
    """One APSW connection and serialized transactions; no general SQL endpoint."""

    def __init__(self, database: str | Path, *, fresh: bool = False, busy_timeout_ms: int = 5000):
        try:
            import apsw  # sole production Python SQLite driver
        except ImportError as exc:
            raise Unavailable("APSW is required for production Studio storage") from exc
        self._mutex = RLock()
        self._domain_key = object()
        self._bindings_open = True
        self._bindings: dict[str, DomainBinding] = {}
        self._conn = apsw.Connection(str(database))
        self._conn.setbusytimeout(busy_timeout_ms)
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=" + str(int(busy_timeout_ms)))
        try:
            if fresh:
                create_new_database(self._conn)
            else:
                ensure_existing_database(self._conn)
            self._registry = NamedRegistry(self._conn)
            self._machines = StateMachines()
        except BaseException:
            self._conn.close()
            raise

    def bind_domain(self, domain: str) -> DomainBinding:
        """Trusted composition ONLY; call seal_bindings before accepting traffic."""
        if not self._bindings_open or domain not in {p.owner for p in self._registry.tables.values()}:
            raise Forbidden("domain binding unavailable")
        if domain not in self._bindings:
            self._bindings[domain] = DomainBinding(domain, self._domain_key)
        return self._bindings[domain]

    def seal_bindings(self) -> None:
        self._bindings_open = False

    def begin(self, *, actor: Mapping[str, Any], project_id: str, request_id: str, domain_owner: DomainBinding) -> "ScopedTransaction":
        return ScopedTransaction.begin(self, actor=actor, project_id=project_id, request_id=request_id, domain_owner=domain_owner)

    def close(self) -> None:
        with self._mutex:
            if not self._conn.getautocommit():
                raise Conflict("cannot close with active transaction")
            self._conn.close()


class ScopedTransaction:
    def __init__(self, store: ScopedStore, *, actor: Actor, project_id: str, request_id: str, owner: str):
        self._store = store
        self.actor = actor
        self.project_id = project_id
        self.request_id = request_id
        self.domain_owner = owner
        self._closed = False

    @classmethod
    def begin(cls, store: ScopedStore, *, actor: Mapping[str, Any], project_id: str, request_id: str, domain_owner: DomainBinding) -> "ScopedTransaction":
        if not isinstance(domain_owner, DomainBinding) or domain_owner._key is not store._domain_key or store._bindings.get(domain_owner.name) is not domain_owner:
            raise Forbidden("untrusted domain binding")
        if not project_id or not isinstance(project_id, str) or not request_id or not isinstance(request_id, str):
            raise Invalid("project_id and request_id are required")
        principal = Actor.from_context(actor)
        if project_id not in principal.allowed_project_ids:
            raise Forbidden("actor is not authorized for requested project")
        # A shared connection must never admit overlapping same-thread transactions.
        store._mutex.acquire()
        try:
            if not store._conn.getautocommit():
                raise Conflict("transaction already open")
            store._conn.execute("BEGIN IMMEDIATE")
        except BaseException:
            store._mutex.release()
            raise
        return cls(store, actor=principal, project_id=project_id, request_id=request_id, owner=domain_owner.name)

    def _assert_open(self) -> None:
        if self._closed:
            raise Conflict("transaction already closed")

    def _execute(self, sql: str, bindings: Mapping[str, Any] | Sequence[Any] = ()) -> Any:
        self._assert_open()
        # Private to C's compiled, identifier-whitelisted command plans.
        return self._store._conn.execute(sql, bindings)

    def commit(self) -> None:
        self._assert_open()
        try:
            self._store._conn.execute("COMMIT")
        except BaseException:
            if not self._store._conn.getautocommit():
                self._store._conn.execute("ROLLBACK")
            raise
        finally:
            self._closed = True
            self._store._mutex.release()

    def rollback(self) -> None:
        self._assert_open()
        try:
            self._store._conn.execute("ROLLBACK")
        finally:
            self._closed = True
            self._store._mutex.release()

    def __enter__(self) -> "ScopedTransaction":
        self._assert_open()
        return self

    def __exit__(self, typ: Any, value: Any, trace: Any) -> None:
        if not self._closed:
            if typ is None:
                self.commit()
            else:
                self.rollback()

    def _rows(self, sql: str, args: Sequence[Any]) -> list[dict[str, Any]]:
        cursor = self._execute(sql, args)
        description = cursor.getdescription()
        names = [d[0] for d in description]
        return [dict(zip(names, r)) for r in cursor]

    def _scope(self, table: TablePlan, filters: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
        safe_keys(table, filters, allow_empty=True)
        assert_project_value(table, filters, self.project_id)
        conditions: list[str] = []
        args: list[Any] = []
        if table.project_scoped:
            conditions.append('t."project_id"=?')
            args.append(self.project_id)
        elif table.name == "blobs":
            conditions.append('EXISTS (SELECT 1 FROM project_blobs pb WHERE pb.project_id=? AND pb.content_digest=t.content_digest)')
            args.append(self.project_id)
        elif table.name == "principals":
            conditions.append('EXISTS (SELECT 1 FROM project_grants g WHERE g.project_id=? AND g.principal_id=t.principal_id)')
            args.append(self.project_id)
        elif table.name == "bootstrap_idempotency":
            conditions.append('t."principal_id"=?')
            args.append(self.actor.principal_id)
        elif table.name not in GLOBAL_CATALOG:
            raise Forbidden("global table is not scoped")
        for col, val in filters.items():
            if col == "project_id":
                continue
            conditions.append(f't."{col}"=?' if val is not None else f't."{col}" IS NULL')
            if val is not None:
                args.append(val)
        return conditions, args

    def select_named(self, command: str, *, keys: Mapping[str, Any] | None = None,
                     filters: Mapping[str, Any] | None = None, limit: int = 100,
                     after: Sequence[Any] | None = None, column: str | None = None) -> Any:
        table, verb = self._store._registry.lookup(command, self.domain_owner, write=False)
        if verb == "get":
            requested = dict(keys or {})
            expected = set(table.primary_key) - ({"project_id"} if table.project_scoped else set())
            if set(requested) != expected:
                raise Invalid("get requires all primary-key components")
            conditions, args = self._scope(table, requested)
            return next(iter(self._rows(f'SELECT t.* FROM "{table.name}" t WHERE {" AND ".join(conditions)} LIMIT 1', args)), None)
        conditions, args = self._scope(table, dict(filters or {}))
        where = " AND ".join(conditions) or "1=1"
        if verb == "count":
            return self._execute(f'SELECT COUNT(*) FROM "{table.name}" t WHERE {where}', args).fetchone()[0]
        if verb == "sum":
            if column not in table.columns or column not in {"byte_size", "priority", "revision", "generation", "amount", "limit_amount", "reserved_amount", "spent_amount", "event_seq"}:
                raise Invalid("unregistered numeric aggregation")
            return self._execute(f'SELECT COALESCE(SUM(t."{column}"),0) FROM "{table.name}" t WHERE {where}', args).fetchone()[0]
        size = bounded_limit(limit)
        cursor = safe_pk_cursor(table, after)
        if cursor is not None:
            columns = ",".join(f't."{c}"' for c in table.primary_key)
            placeholders = ",".join("?" for _ in cursor)
            where += f' AND ({columns}) > ({placeholders})'
            args.extend(cursor)
        order = ",".join(f't."{c}"' for c in table.primary_key)
        return self._rows(f'SELECT t.* FROM "{table.name}" t WHERE {where} ORDER BY {order} LIMIT ?', [*args, size])

    def execute_named(self, command: str, *, values: Mapping[str, Any] | None = None,
                      keys: Mapping[str, Any] | None = None, expected_revision: int | None = None) -> int:
        table, verb = self._store._registry.lookup(command, self.domain_owner, write=True)
        if table.name == "blobs":
            raise Forbidden("blob metadata writes require physical content verification")
        if table.name == "relay_sync_cursors" and verb == "update":
            raise Forbidden("relay cursor updates require verified traversal journal")
        if table.name in GLOBAL_RESTRICTED and table.name != "blobs" and verb == "delete":
            raise Forbidden("deleting restricted global identity/idempotency data is prohibited")
        if verb == "insert":
            payload = dict(values or {})
            safe_keys(table, payload)
            assert_project_value(table, payload, self.project_id)
            if table.project_scoped:
                payload["project_id"] = self.project_id
            if table.name == "bootstrap_idempotency" and payload.get("principal_id") != self.actor.principal_id:
                raise Forbidden("bootstrap request owner mismatch")
            fields = list(payload)
            sql = f'INSERT INTO "{table.name}" ({",".join(chr(34)+x+chr(34) for x in fields)}) VALUES ({",".join("?" for _ in fields)})'
            self._execute(sql, list(payload.values()))
            return 1
        if table.name in {"audit_events", "owner_decisions", "approval_consumptions", "effect_intents", "qa_reviews", "qa_receipts", "qa_independence_proofs", "relay_observations", "relay_receipts", "bible_editions", "canon_snapshots", "artifacts", "source_revisions", "backup_sets", "idempotency", "bootstrap_idempotency", "blobs", "projects"} and verb == "delete":
            raise Forbidden("protected durable evidence cannot be deleted via generic commands")
        key_values = dict(keys or {})
        key_columns = set(table.primary_key) - ({"project_id"} if table.project_scoped else set())
        if set(key_values) != key_columns:
            raise Invalid("write requires complete primary key")
        where, bind = self._scope(table, key_values)
        if table.name in GLOBAL_RESTRICTED and table.name != "blobs":
            raise Forbidden("restricted global mutation requires bootstrap/identity service")
        if verb == "delete":
            cur = self._execute(f'DELETE FROM "{table.name}" AS t WHERE {" AND ".join(where)} RETURNING 1', bind)
            if cur.fetchone() is None:
                raise Missing("no matching scoped row")
            return 1
        payload = dict(values or {})
        safe_keys(table, payload)
        assert_project_value(table, payload, self.project_id)
        if set(payload) & set(table.primary_key):
            raise Invalid("primary key cannot be updated")
        machine_col = self._store._machines.state_column(table.name)
        if table.name in self._store._machines.transitions and machine_col in payload:
            previous_row = self._execute(f'SELECT t."{machine_col}" FROM "{table.name}" t WHERE {" AND ".join(where)} LIMIT 1', bind).fetchone()
            if previous_row is None:
                raise Conflict("state transition row absent or out of scope")
            self._store._machines.verify(table.name, previous_row[0], payload[machine_col])
        if table.revisioned:
            if not isinstance(expected_revision, int) or expected_revision < 1 or "revision" in payload:
                raise Invalid("revisioned update requires expected_revision; revision advances automatically")
            where.append('"revision"=?')
            bind.append(expected_revision)
        assignments = [f'"{x}"=?' for x in payload]
        if table.revisioned:
            assignments.append('"revision"="revision"+1')
        cur = self._execute(f'UPDATE "{table.name}" AS t SET {",".join(assignments)} WHERE {" AND ".join(where)} RETURNING 1', [*payload.values(), *bind])
        if cur.fetchone() is None:
            raise Conflict("stale revision, fencing token or scoped row")
        return 1

    def cas_lease(self, *, task_id: str, generation: int, old_status: str, new_status: str, expires_at: str | None = None) -> None:
        """WG-D only: lease generation is part of compare-and-swap fence."""
        if self.domain_owner != "WG-D" or generation < 1 or old_status not in ("active", "expired", "released") or new_status not in ("active", "expired", "released"):
            raise Forbidden("invalid lease write authority or state")
        update = 'status=?' + (',expires_at=?' if expires_at is not None else '')
        params: list[Any] = [new_status]
        if expires_at is not None:
            params.append(expires_at)
        cur = self._execute(f'UPDATE leases SET {update} WHERE project_id=? AND task_id=? AND generation=? AND status=? RETURNING 1',
                            [*params, self.project_id, task_id, generation, old_status])
        if cur.fetchone() is None:
            raise Conflict("lease generation/status fence rejected")
