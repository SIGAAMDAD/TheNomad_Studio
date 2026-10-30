"""C-backed D scheduling and task transaction port.

D's SQL is statically authored in ProductionService. This adapter enforces
project fences on every query, restricts DML to D-owned tables, and shares C's
single atomic writer/idempotency journal with worker shifts and resource ports.
"""
from __future__ import annotations

import re
from typing import Any, Callable, TypeVar

from studio.production.service import ProductionFault
from studio.workforce import WorkforceFault
from .schema import table_owners
from .registry import GLOBAL_CATALOG
from .workforce import WorkforceStorage, WorkforceTransaction

T = TypeVar("T")
_DML = re.compile(r"^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)\b", re.I)


class ProductionTx:
    def __init__(self, raw: WorkforceTransaction, project_id: str,
                 write: bool, owners: dict[str, str]):
        self._raw = raw
        self._project = project_id
        self._write = write
        self._owners = owners

    def _check(self, sql: str, params: tuple[Any, ...], *, write: bool = False) -> None:
        if not isinstance(sql, str):
            raise ProductionFault("forbidden", "invalid D SQL")
        if "project_id" not in sql.lower():
            # The frozen schema contains global specialist/tool catalogs used
            # during task eligibility checks. Reads may reference those only.
            referenced = re.findall(r"\\b(?:FROM|JOIN)\\s+([a-z_]+)\\b", sql, re.I)
            if (write or not sql.lstrip().upper().startswith("SELECT ")
                    or not referenced or any(t.lower() not in GLOBAL_CATALOG for t in referenced)):
                raise ProductionFault("forbidden", "D operation requires project-scoped SQL")
        elif self._project not in params:
            raise ProductionFault("forbidden", "D statement missing bound project")
        match = _DML.match(sql)
        if write:
            if not self._write:
                raise ProductionFault("forbidden", "D writes prohibited in read transaction")
            if match is None or self._owners.get(match.group(2).lower()) != "WG-D":
                raise ProductionFault("forbidden", "D cannot modify unowned table")
        elif match is not None:
            raise ProductionFault("forbidden", "D SQL mutation requires write command")

    def one(self, sql: str, params: tuple[Any, ...] = ()):
        self._check(sql, params)
        return self._raw.one(sql, params)

    def all(self, sql: str, params: tuple[Any, ...] = ()):
        self._check(sql, params)
        return self._raw.all(sql, params)

    def change(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self._check(sql, params, write=True)
        return self._raw.change(sql, params)


class ProductionAPSWStorage:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("C-backed durable APSW writer required")
        self._writer = writer
        self._owners = table_owners()

    def read(self, project_id: str, fn: Callable[[ProductionTx], T]) -> T:
        return self._writer.read(project_id,
            lambda raw: fn(ProductionTx(raw, project_id, False, self._owners)))

    def command(self, project_id: str, operation: str, request_id: str,
                payload_sha256: str, fn: Callable[[ProductionTx], dict]) -> dict:
        if not operation.startswith("D."):
            raise ProductionFault("forbidden", "unregistered production command owner")
        try:
            return self._writer.command(project_id, operation, request_id, payload_sha256,
                lambda raw: fn(ProductionTx(raw, project_id, True, self._owners)))
        except WorkforceFault as error:
            raise ProductionFault(
                {"unknown":"unknown_effect","conflict":"conflict","invalid":"invalid",
                 "forbidden":"forbidden"}.get(error.code,"internal"), str(error)) from error
