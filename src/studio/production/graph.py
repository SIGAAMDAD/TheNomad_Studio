"""Deterministic bounded DAG algorithms; every edge (task, prerequisite)."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
import heapq
from typing import Iterable, Mapping


class GraphFault(ValueError):
    pass


@dataclass(frozen=True)
class TaskGraph:
    prerequisites: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        nodes = set(self.prerequisites)
        if not nodes:
            raise GraphFault('empty graph')
        for key, deps in self.prerequisites.items():
            if not key or not isinstance(key, str):
                raise GraphFault('invalid task key')
            if any(dep not in nodes or dep == key for dep in deps):
                raise GraphFault('unknown or self dependency')
        self.order()  # fail fast on cycles

    @classmethod
    def from_specs(cls, specs: Iterable[object]) -> 'TaskGraph':
        rows = list(specs)
        graph = {s.key: frozenset(s.depends_on) for s in rows}
        if len(graph) != len(rows):
            raise GraphFault('duplicate task key')
        return cls(graph)

    def order(self) -> tuple[str, ...]:
        outgoing: dict[str, set[str]] = defaultdict(set)
        incoming = {key: len(deps) for key, deps in self.prerequisites.items()}
        for task, deps in self.prerequisites.items():
            for dep in deps:
                outgoing[dep].add(task)
        ready = [key for key, degree in incoming.items() if degree == 0]
        heapq.heapify(ready)
        order = []
        while ready:
            node = heapq.heappop(ready)
            order.append(node)
            for consumer in sorted(outgoing[node]):
                incoming[consumer] -= 1
                if incoming[consumer] == 0:
                    heapq.heappush(ready, consumer)
        if len(order) != len(incoming):
            raise GraphFault('dependency cycle')
        return tuple(order)

    def ready(self, accepted: set[str], eligible: set[str] | None = None, *, limit: int = 128) -> tuple[str, ...]:
        if limit <= 0 or limit > 4096:
            raise GraphFault('invalid readiness limit')
        if accepted - self.prerequisites.keys():
            raise GraphFault('unknown accepted task')
        eligible = set(self.prerequisites) if eligible is None else eligible
        if eligible - self.prerequisites.keys():
            raise GraphFault('unknown eligible task')
        return tuple(n for n in self.order()
                     if n not in accepted and n in eligible and self.prerequisites[n] <= accepted)[:limit]

    def critical_path(self, durations: Mapping[str, int]) -> tuple[int, tuple[str, ...]]:
        if set(durations) != set(self.prerequisites) or any(type(t) is not int or t <= 0 for t in durations.values()):
            raise GraphFault('positive duration for every task required')
        scores: dict[str, int] = {}
        paths: dict[str, tuple[str, ...]] = {}
        for key in self.order():
            options = [(scores[d], paths[d]) for d in self.prerequisites[key]]
            longest, path = min(options, key=lambda x: (-x[0], x[1])) if options else (0, ())
            scores[key] = longest + durations[key]
            paths[key] = path + (key,)
        winner = sorted(scores, key=lambda k: (-scores[k], paths[k]))[0]
        return scores[winner], paths[winner]
