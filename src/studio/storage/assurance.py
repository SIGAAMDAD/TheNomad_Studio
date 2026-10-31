"""C-owned APSW transaction adapter for E independent QA and oversight.

Only trusted E service code can call execute(); the adapter enforces E table
ownership, the project fence, atomic audit events, and exact idempotent replay.
External verification and reviewer selection remain separate trusted ports.
"""
from __future__ import annotations

import re
from hashlib import sha256
from collections.abc import Sequence
from typing import Any, Callable, TypeVar

from studio.assurance.service import AssuranceError
from studio.workforce import WorkforceFault
from .schema import table_owners
from .workforce import WorkforceStorage, WorkforceTransaction

T = TypeVar("T")
_DML = re.compile(r"^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)\b", re.I)


class AssuranceTx:
    def __init__(self, raw: WorkforceTransaction, project_id: str,
                 principal_id: str | None, owners: dict[str, str]):
        self._raw = raw
        self.project_id = project_id
        self.principal_id = principal_id
        self._owners = owners

    def _check_query(self, sql: str, params: Sequence[Any]) -> None:
        # E's SQL is authored by trusted source; no request can supply SQL.
        # Refuse a statement without an explicit project fence.
        if not isinstance(sql, str):
            raise AssuranceError("forbidden", "E query must be verified SQL")
        if "project_id" not in sql.lower():
            # E's independent-reviewer gate reads the global J principal kind,
            # then separately verifies current A project grants and lineage.
            # No other unscoped source query is authorized here.
            normalized = " ".join(sql.split()).lower()
            if normalized != "select kind from principals where principal_id=?":
                raise AssuranceError("forbidden", "E query lacks project fence")
        elif self.project_id not in params:
            raise AssuranceError("forbidden", "E query does not bind its project")

    def one(self, sql: str, params: Sequence[Any] = ()):
        self._check_query(sql, params)
        return self._raw.one(sql, tuple(params))

    def all(self, sql: str, params: Sequence[Any] = ()):
        self._check_query(sql, params)
        return self._raw.all(sql, tuple(params))

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        self._check_query(sql, params)
        match = _DML.match(sql)
        if match is None or self._owners.get(match.group(2).lower()) != "WG-E":
            raise AssuranceError("forbidden", "E cannot mutate another domain's table")
        if self.principal_id is None:
            raise AssuranceError("forbidden", "read-only E transaction cannot mutate")
        self._raw.change(sql, tuple(params))

    def event(self, *, event_type: str, subject_id: str, payload_digest: str) -> None:
        if self.principal_id is None:
            raise AssuranceError("forbidden", "read-only E transaction cannot append events")
        if (not all(isinstance(x, str) and x for x in
                    (event_type, subject_id, payload_digest))
                or len(payload_digest) != 64):
            raise AssuranceError("invalid", "invalid audit event")
        seq = self._raw.one(
            "SELECT COALESCE(MAX(event_seq),0)+1 AS next_seq FROM audit_events WHERE project_id=?",
            (self.project_id,))["next_seq"]
        self._raw.change(
            "INSERT INTO audit_events(project_id,event_seq,event_type,subject_type,"
            "subject_id,payload_digest,actor_principal_id) VALUES(?,?,?,'assurance',?,?,?)",
            (self.project_id, seq, event_type, subject_id, payload_digest, self.principal_id))


class AssuranceAPSWStore:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("C-backed durable APSW writer required")
        self._writer = writer
        self._owners = table_owners()

    def read(self, *, project_id: str, query: Callable[[AssuranceTx], T]) -> T:
        return self._writer.read(
            project_id, lambda raw: query(AssuranceTx(raw, project_id, None, self._owners)))

    def run(self, *, project_id: str, domain: str, operation: str,
            principal_id: str, request_id: str, request_digest: str,
            command: Callable[[AssuranceTx], dict]) -> dict:
        if domain != "E" or not operation or not principal_id:
            raise AssuranceError("forbidden", "invalid E operation authority")
        bound = sha256((principal_id + ":" + request_digest).encode()).hexdigest()
        try:
            return self._writer.command(project_id, "E." + operation, request_id, bound,
                lambda raw: command(AssuranceTx(raw, project_id, principal_id, self._owners)))
        except WorkforceFault as error:
            raise AssuranceError(
                {"unknown": "unknown_effect", "conflict": "conflict",
                 "invalid": "invalid", "forbidden": "forbidden"}.get(error.code, "internal"),
                str(error)) from error
