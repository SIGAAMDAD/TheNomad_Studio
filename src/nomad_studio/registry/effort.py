"""Finite effort selection, driven only by observed provider offerings."""
from __future__ import annotations

from collections.abc import Sequence

TIERS = ('instant','medium','high','extra_high')


def select_effort(task_class: str, offered: Sequence[str], *, persistent: bool = True) -> str | None:
    """None means cannot dispatch; no imaginary UI-level effort activation."""
    if (not isinstance(task_class,str) or task_class not in
        ('production','lead','qa','debug','status') or
        isinstance(offered,(str,bytes)) or
        not isinstance(offered,Sequence) or
        any(t not in TIERS for t in offered)):
        raise ValueError('invalid task class or provider effort manifest')
    available=set(offered)
    if not persistent and task_class == 'status':
        for tier in ('medium','high','extra_high','instant'):
            if tier in available:
                return tier
    if task_class in ('lead','qa','debug') and 'extra_high' in available:
        return 'extra_high'
    if 'high' in available:
        return 'high'
    if 'extra_high' in available:
        return 'extra_high'
    return None
