"""WG-C-backed, project-fenced APSW persistence for WG-B organization commands.

This is a trusted coordinator-side adapter: worker chats cannot submit SQL or
table identifiers. It shares the same single physical writer as workforce
and D scheduling instead of installing a separate organization database.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Any, Callable, TypeVar

from studio.organization.ports import OWNED_TABLES
from studio.organization.verification import OrganizationError

from .registry import TablePlan
from .schema import table_owners
from .workforce import WorkforceStorage, WorkforceTransaction
from studio.workforce import WorkforceFault

T = TypeVar('T')
MAX_ORGANIZATION_ROWS = 10000


class _OrganizationTx:
    def __init__(self, tx: WorkforceTransaction, project_id: str,
                 plans: Mapping[str, TablePlan]):
        self._tx, self._project, self._plans = tx, project_id, plans

    def _table(self, name: str) -> TablePlan:
        if not isinstance(name, str) or name not in self._plans:
            raise OrganizationError('forbidden', 'table not owned by WG-B')
        return self._plans[name]

    def _fields(self, plan: TablePlan, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping) or any(
                not isinstance(key, str) or key not in plan.columns for key in values):
            raise OrganizationError('invalid', 'unregistered organization column')
        result = dict(values)
        if plan.project_scoped:
            if 'project_id' in result and result['project_id'] != self._project:
                raise OrganizationError('forbidden', 'cross-project organization row')
            result['project_id'] = self._project
        return result

    @staticmethod
    def _where(values: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]]:
        if not values:
            return '1=1', ()
        return (' AND '.join('"'+field+'"=?' if value is not None
                             else '"'+field+'" IS NULL'
                             for field, value in values.items()),
                tuple(value for value in values.values() if value is not None))

    def many(self, table: str, **keys: Any) -> list[dict[str, Any]]:
        plan = self._table(table)
        query = self._fields(plan, keys)
        where, args = self._where(query)
        order = ','.join('"'+col+'"' for col in plan.primary_key)
        rows = self._tx.all(
            f'SELECT * FROM "{plan.name}" WHERE {where} ORDER BY {order} LIMIT ?',
            (*args, MAX_ORGANIZATION_ROWS + 1))
        if len(rows) > MAX_ORGANIZATION_ROWS:
            raise OrganizationError('capability_unavailable', 'organization query exceeded bounded page')
        return rows

    def one(self, table: str, **keys: Any) -> dict[str, Any] | None:
        # A query that does not uniquely identify a record is an integration
        # defect, not an arbitrary first-row choice.
        rows = self.many(table, **keys)
        if len(rows) > 1:
            raise OrganizationError('conflict', 'organization selector was not unique')
        return rows[0] if rows else None

    def insert(self, table: str, record: Mapping[str, Any]) -> None:
        plan = self._table(table)
        fields = self._fields(plan, record)
        if not fields:
            raise OrganizationError('invalid', 'empty organization insert')
        columns = ','.join('"'+field+'"' for field in fields)
        holders = ','.join('?' for _ in fields)
        self._tx.change(f'INSERT INTO "{plan.name}" ({columns}) VALUES ({holders})',
                        tuple(fields.values()))

    def change(self, table: str, keys: Mapping[str, Any],
               values: Mapping[str, Any]) -> None:
        plan = self._table(table)
        selector = self._fields(plan, keys)
        if set(selector) != set(plan.primary_key):
            raise OrganizationError('invalid', 'organization mutation requires complete primary key')
        changed = self._fields(plan, values)
        # Project id is automatically added for filtering; it must not change.
        if plan.project_scoped:
            changed.pop('project_id')
        if not changed or set(changed).intersection(plan.primary_key):
            raise OrganizationError('invalid', 'primary key mutation forbidden')
        where, args = self._where(selector)
        assignments = ','.join('"'+field+'"=?' for field in changed)
        affected = self._tx.change(
            f'UPDATE "{plan.name}" SET {assignments} WHERE {where}',
            (*changed.values(), *args))
        if affected != 1:
            raise OrganizationError('missing', 'organization row not found or changed')


class OrganizationAPSWStore:
    """Satisfies WG-B OrganizationStore using C's existing atomic command ledger."""

    def __init__(self, storage: WorkforceStorage):
        if not isinstance(storage, WorkforceStorage):
            raise TypeError('real C-owned workforce/production APSW writer required')
        self._storage = storage
        owners = table_owners()
        if any(owners.get(name) != 'WG-B' for name in OWNED_TABLES):
            raise ValueError('organization ownership does not match frozen schema')
        plans: dict[str, TablePlan] = {}
        with storage._lock:
            for table in sorted(OWNED_TABLES):
                columns = list(storage._connection.execute(f'PRAGMA table_info("{table}")'))
                if not columns:
                    raise ValueError('missing organization table: '+table)
                pk = tuple(row[1] for row in sorted(columns, key=lambda x:x[5]) if row[5]>0)
                names = frozenset(row[1] for row in columns)
                plans[table] = TablePlan(table, 'WG-B', names, pk,
                                         'project_id' in names, 'revision' in names)
        self._plans = plans

    @staticmethod
    def _error(error: WorkforceFault) -> OrganizationError:
        code = {'unknown':'unknown_effect', 'invalid':'invalid',
                'conflict':'conflict', 'forbidden':'forbidden'}.get(error.code,'internal')
        return OrganizationError(code, str(error))

    def read(self, project_id: str, action: Callable[[_OrganizationTx], T]) -> T:
        try:
            return self._storage.read(project_id, lambda tx: action(
                _OrganizationTx(tx, project_id, self._plans)))
        except WorkforceFault as error:
            raise self._error(error) from error

    def mutate(self, *, project_id: str, principal_id: str, action_name: str,
               request_id: str, payload_digest: str,
               action: Callable[[_OrganizationTx], dict]) -> dict:
        if (not isinstance(principal_id, str) or not principal_id
                or not isinstance(action_name, str) or not action_name.startswith('B.')
                or not isinstance(payload_digest, str) or len(payload_digest)!=64):
            raise OrganizationError('invalid', 'invalid organization command identity')
        # Bind the authenticated principal to the C idempotency key and digest.
        bytes_to_bind = json.dumps(
            {'principal_id':principal_id,'action':action_name,'digest':payload_digest},
            sort_keys=True,separators=(',',':')).encode('utf-8')
        command_digest = sha256(bytes_to_bind).hexdigest()
        try:
            return self._storage.command(
                project_id, action_name, request_id, command_digest,
                lambda tx: action(_OrganizationTx(tx, project_id, self._plans)))
        except WorkforceFault as error:
            raise self._error(error) from error
