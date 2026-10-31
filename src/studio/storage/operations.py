"""C-owned verified named SQL plan executor for F operations.

Every SQL text is installed in F's checked source plan catalog. Requests pass
named values only; no caller-provided SQL or table names are permitted. C owns
the single durable mutation/idempotency transaction and per-project audit cursor.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Callable, TypeVar

from studio.operations.core import Scope, DomainError
from studio.operations.plans import plans
from studio.workforce import WorkforceFault
from .workforce import WorkforceStorage, WorkforceTransaction
from .schema import table_owners

T = TypeVar("T")
_MAX_READ_ROWS = 25000


class FTransaction:
    def __init__(self, raw: WorkforceTransaction, scope: Scope,
                 *, write: bool, plans_by_name: dict):
        self._raw, self._scope, self._write = raw, scope, write
        self._plans = plans_by_name

    def _resolve(self, name: str, args: Mapping[str, Any], kind: str):
        row = self._plans.get(name)
        if row is None or row[0] != kind or not name.startswith("f."):
            raise DomainError("forbidden", "unregistered F plan or wrong execution mode")
        if not isinstance(args, Mapping) or args.get("project_id") != self._scope.project_id:
            raise DomainError("forbidden", "F query lacks matching project scope")
        # All SQL is fixed at import time; reject unknown and missing bindings.
        import re
        names = set(re.findall(r":([a-z_][a-z_0-9]*)", row[1]))
        if set(args) != names:
            raise DomainError("invalid", "F SQL parameter set differs from named plan")
        return row[1], dict(args)

    def select_named(self, name: str, parameters: Mapping[str, Any]) -> list:
        sql, args = self._resolve(name, parameters, "select")
        # C's query executes with bound named values, without interpolating SQL.
        rows = self._raw.all(sql, args)
        if len(rows) > _MAX_READ_ROWS:
            raise DomainError("capability_unavailable", "F query exceeded read bound")
        return rows

    def execute_named(self, name: str, parameters: Mapping[str, Any]) -> int:
        if not self._write:
            raise DomainError("forbidden", "cannot mutate in F read transaction")
        sql, args = self._resolve(name, parameters, "execute")
        return self._raw.change(sql, args)

    def record_event(self, *, event_type: str, subject_type: str,
                     subject_id: str, payload_digest: str) -> None:
        if not self._write:
            raise DomainError("forbidden", "cannot emit audit event in read transaction")
        if any(not isinstance(x, str) or not x for x in
               (event_type, subject_type, subject_id, payload_digest)):
            raise DomainError("invalid", "invalid F event")
        seq = self._raw.one(
            "SELECT COALESCE(MAX(event_seq),0)+1 AS seq FROM audit_events WHERE project_id=?",
            (self._scope.project_id,))["seq"]
        self._raw.change(
            "INSERT INTO audit_events(project_id,event_seq,event_type,subject_type,"
            "subject_id,payload_digest,actor_principal_id) VALUES(?,?,?,?,?,?,?)",
            (self._scope.project_id, seq, event_type, subject_type, subject_id,
             payload_digest, self._scope.principal_id))


class OperationsAPSWRunner:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("C-backed APSW writer required")
        self._writer = writer
        self._plans = plans()
        owners = table_owners()
        import re
        for name, (kind, sql) in self._plans.items():
            if kind == "execute":
                match = re.match(r"\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)", sql, re.I)
                if match is None or owners.get(match[1].lower()) != "WG-F":
                    raise ValueError("F has unowned write plan: " + name)

    @staticmethod
    def _scope(scope: Scope) -> None:
        if not isinstance(scope, Scope):
            raise DomainError("forbidden", "authenticated F Scope required")
        scope.check()

    def query(self, scope: Scope, fn: Callable[[FTransaction], T]) -> T:
        self._scope(scope)
        return self._writer.read(scope.project_id,
             lambda raw: fn(FTransaction(raw, scope, write=False, plans_by_name=self._plans)))

    def command(self, scope: Scope, *, owner: str, request_id: str,
                body_digest: str, fn: Callable[[FTransaction], dict]) -> dict:
        self._scope(scope)
        if owner != "F" or not isinstance(body_digest, str) or len(body_digest) != 64:
            raise DomainError("forbidden", "invalid F command owner or digest")
        bound = sha256((scope.principal_id + ":" + body_digest).encode("utf-8")).hexdigest()
        try:
            return self._writer.command(scope.project_id, "F.command", request_id, bound,
                lambda raw: fn(FTransaction(raw, scope, write=True, plans_by_name=self._plans)))
        except WorkforceFault as error:
            raise DomainError({"unknown":"unknown_effect","conflict":"conflict",
                               "invalid":"invalid","forbidden":"forbidden"}.get(error.code,"internal"),
                              str(error)) from error
