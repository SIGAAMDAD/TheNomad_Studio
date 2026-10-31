"""Indexed, bounded SQL production aggregations and task dependency traversal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .engine import ScopedTransaction
from .errors import Forbidden, Invalid
from .registry import bounded_limit


@dataclass(frozen=True)
class QueueCounts:
    pending: int
    ready: int
    leased: int
    running: int


class ProductionQueries:
    @staticmethod
    def task_counts(tx: ScopedTransaction) -> QueueCounts:
        if tx.domain_owner not in ("WG-D", "WG-F"):
            raise Forbidden("task queue projection unavailable to domain")
        rows = tx._execute("SELECT state,COUNT(*) FROM tasks WHERE project_id=? "
                           "AND state IN ('pending','ready','leased','running') GROUP BY state", (tx.project_id,))
        counts = dict(rows)
        return QueueCounts(*(counts.get(key, 0) for key in ("pending", "ready", "leased", "running")))

    @staticmethod
    def ready_tasks(tx: ScopedTransaction, *, limit: int = 100) -> list[dict[str, Any]]:
        if tx.domain_owner != "WG-D":
            raise Forbidden("task scheduling belongs to WG-D")
        size = bounded_limit(limit)
        return tx._rows("SELECT t.* FROM tasks t WHERE t.project_id=? AND t.state='ready' "
                        "AND NOT EXISTS (SELECT 1 FROM task_dependencies d LEFT JOIN tasks p "
                        "ON p.project_id=d.project_id AND p.task_id=d.depends_on_task_id "
                        "WHERE d.project_id=t.project_id AND d.task_id=t.task_id AND p.state<>'accepted') "
                        "ORDER BY t.priority DESC,t.task_id LIMIT ?", (tx.project_id, size))

    @staticmethod
    def would_create_dependency_cycle(tx: ScopedTransaction, *, task_id: str, depends_on_task_id: str,
                                      maximum_reachable: int = 10000) -> bool:
        if tx.domain_owner != "WG-D":
            raise Forbidden("task graph belongs to WG-D")
        if task_id == depends_on_task_id:
            return True
        if not isinstance(maximum_reachable, int) or not 1 <= maximum_reachable <= 100000:
            raise Invalid("invalid graph traversal bound")
        # UNION (distinct) terminates on existing cycles; the count guard makes
        # oversized graphs fail closed rather than silently ignoring deep paths.
        sql = "WITH RECURSIVE reachable(id) AS (SELECT ? UNION SELECT d.depends_on_task_id "
        sql += "FROM task_dependencies d JOIN reachable r ON d.task_id=r.id WHERE d.project_id=?) "
        sql += "SELECT COUNT(*),COALESCE(MAX(CASE WHEN id=? THEN 1 ELSE 0 END),0) FROM reachable"
        visited, found = tx._execute(sql, (depends_on_task_id, tx.project_id, task_id)).fetchone()
        if visited > maximum_reachable:
            raise Invalid("dependency graph exceeds configured traversal bound")
        return bool(found)
