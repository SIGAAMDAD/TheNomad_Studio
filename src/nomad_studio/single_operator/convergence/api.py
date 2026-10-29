"""Derived, side-effect-free cross-cabal dependency view; no legacy commands."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any


def unresolved_dependencies(recommendation: Mapping[str, Any]) -> tuple[str, ...]:
    """Return dependencies for which no council proposal has been selected.

    This never changes a bible, graph, source, or QA state; only CouncilPort
    owns council cases. The caller supplies a verified get_recommendation record.
    """
    if recommendation.get('state') == 'RESOLVED':
        return ()
    conflict = recommendation.get('conflict')
    if not isinstance(conflict, Mapping):
        return ()
    dependencies = conflict.get('dependency_refs', ())
    if not isinstance(dependencies, (list, tuple)):
        return ()
    return tuple(sorted({item for item in dependencies if isinstance(item, str)}))
