"""Whitelisted named, project-scoped command catalog; never accept caller SQL."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .errors import Forbidden, Invalid
from .schema import table_owners

MAX_BATCH = 250


@dataclass(frozen=True)
class TablePlan:
    name: str
    owner: str
    columns: frozenset[str]
    primary_key: tuple[str, ...]
    project_scoped: bool
    revisioned: bool


# These global tables do not have project_id; they require special handling.
# Only truly global catalog data may be scanned. Blob/principal references remain scoped.
GLOBAL_CATALOG = frozenset({"departments", "package_revisions", "specialist_roles", "package_dependencies", "tool_entrypoints"})
GLOBAL_RESTRICTED = frozenset({"blobs", "principals", "bootstrap_idempotency"})


class NamedRegistry:
    def __init__(self, connection: Any):
        owners = table_owners()
        self.tables: dict[str, TablePlan] = {}
        for name, owner in owners.items():
            info = list(connection.execute(f'PRAGMA table_info("{name}")'))
            if not info:
                raise Invalid(f"missing table {name}")
            fields = frozenset(str(x[1]) for x in info)
            primary = tuple(x[1] for x in sorted(info, key=lambda r: r[5]) if x[5] > 0)
            self.tables[name] = TablePlan(name, owner, fields, primary, "project_id" in fields, "revision" in fields)
        global_names = {name for name, table in self.tables.items() if not table.project_scoped}
        if global_names != GLOBAL_CATALOG | GLOBAL_RESTRICTED:
            raise Invalid(f"unknown global tables: {global_names ^ (GLOBAL_CATALOG | GLOBAL_RESTRICTED)}")

    def lookup(self, command: str, owner: str, *, write: bool) -> tuple[TablePlan, str]:
        if not isinstance(command, str) or command.count(".") != 1:
            raise Invalid("expected a named table.operation command")
        table_name, verb = command.split(".", 1)
        table = self.tables.get(table_name)
        if table is None or table.owner != owner:
            raise Forbidden("command unavailable to this domain")
        valid = {"insert", "update", "delete"} if write else {"get", "scan", "count", "sum"}
        if verb not in valid:
            raise Forbidden("unregistered command")
        return table, verb


def safe_keys(table: TablePlan, data: Mapping[str, Any], *, allow_empty: bool = False) -> None:
    if not isinstance(data, Mapping) or (not data and not allow_empty):
        raise Invalid("expected a nonempty named parameter mapping")
    if not set(data).issubset(table.columns):
        raise Invalid(f"invalid columns for {table.name}")
    if any(not isinstance(k, str) for k in data):
        raise Invalid("column keys must be strings")


def assert_project_value(table: TablePlan, data: Mapping[str, Any], project_id: str) -> None:
    if table.project_scoped and "project_id" in data and data["project_id"] != project_id:
        raise Forbidden("cross-project value")


def bounded_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= MAX_BATCH):
        raise Invalid(f"limit must be 1..{MAX_BATCH}")
    return limit


def safe_pk_cursor(table: TablePlan, after: Sequence[Any] | None) -> tuple[Any, ...] | None:
    if after is None:
        return None
    if not isinstance(after, (tuple, list)) or len(after) != len(table.primary_key):
        raise Invalid("cursor must contain exact primary key tuple")
    return tuple(after)
