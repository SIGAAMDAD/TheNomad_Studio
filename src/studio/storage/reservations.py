"""WG-F reservations committed atomically inside WG-D lease transactions.

This is a restricted C-owned port. The ProductionTx never obtains general F
DML authority; only these fixed resource plans can modify F budget state.
"""
from __future__ import annotations

from hashlib import sha256

from studio.production.service import ProductionFault
from .production import ProductionTx


class AtomicBudgetReservations:
    @staticmethod
    def _raw(tx: ProductionTx):
        if not isinstance(tx, ProductionTx) or not tx._write:
            raise ProductionFault("forbidden", "F reservation requires active D write transaction")
        return tx._raw

    def reserve(self, tx: ProductionTx, *, project_id: str,
                operation_id: str, resource_amounts: tuple[tuple[str, int], ...]) -> None:
        raw = self._raw(tx)
        if project_id != tx._project or not operation_id or not resource_amounts:
            raise ProductionFault("forbidden", "reservation outside authorized lease project")
        for resource_key, value in resource_amounts:
            if (not isinstance(resource_key, str) or not resource_key or
                    type(value) is not int or value <= 0):
                raise ProductionFault("invalid", "invalid resource amount")
            budgets = raw.all(
                "SELECT budget_id FROM budgets WHERE project_id=? AND resource_key=? "
                "AND limit_amount-spent_amount-reserved_amount>=? ORDER BY budget_id LIMIT 1",
                (project_id, resource_key, value))
            if not budgets:
                raise ProductionFault("conflict", "insufficient approved resource budget")
            budget_id = budgets[0]["budget_id"]
            updated = raw.change(
                "UPDATE budgets SET reserved_amount=reserved_amount+? "
                "WHERE project_id=? AND budget_id=? "
                "AND limit_amount-spent_amount-reserved_amount>=?",
                (value, project_id, budget_id, value))
            if updated != 1:
                raise ProductionFault("conflict", "resource reservation CAS failed")
            reservation_id = sha256(
                f"{project_id}:{operation_id}:{resource_key}".encode()).hexdigest()
            raw.change(
                "INSERT INTO reservations(project_id,reservation_id,budget_id,operation_id,amount,state) "
                "VALUES(?,?,?,?,?,'reserved')",
                (project_id, reservation_id, budget_id, operation_id, value))

    def release(self, tx: ProductionTx, *, project_id: str, operation_id: str) -> None:
        raw = self._raw(tx)
        if project_id != tx._project or not operation_id:
            raise ProductionFault("forbidden", "release outside authorized lease project")
        rows = raw.all(
            "SELECT reservation_id,budget_id,amount FROM reservations WHERE project_id=? "
            "AND operation_id=? AND state='reserved' ORDER BY reservation_id",
            (project_id, operation_id))
        for row in rows:
            affected = raw.change(
                "UPDATE reservations SET state='released' "
                "WHERE project_id=? AND reservation_id=? AND state='reserved'",
                (project_id, row["reservation_id"]))
            if affected != 1:
                raise ProductionFault("conflict", "reservation release CAS failed")
            if raw.change(
                "UPDATE budgets SET reserved_amount=reserved_amount-? "
                "WHERE project_id=? AND budget_id=? AND reserved_amount>=?",
                (row["amount"], project_id, row["budget_id"], row["amount"])) != 1:
                raise ProductionFault("conflict", "budget release CAS failed")
