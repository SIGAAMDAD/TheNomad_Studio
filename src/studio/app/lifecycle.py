"""Deterministic Studio process lifecycle. Browser chats are never started here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .errors import StudioError


@dataclass(frozen=True)
class Component:
    name: str
    recover: Callable[[], None]
    start: Callable[[], None]
    stop: Callable[[], None]


class StudioRuntime:
    """Recovery is complete before any component starts observing relays.

    Start order must be supplied by composition: C database -> G authenticated
    readback -> I projection recovery -> D preparation -> J API. Hooks stop in
    reverse order. An error fails closed and tears down already started hooks.
    """

    def __init__(self, components: list[Component]):
        if len({c.name for c in components}) != len(components):
            raise StudioError("invalid", "duplicate service lifecycle component")
        self.components = tuple(components)
        self._started: list[Component] = []
        self.ready = False

    def start(self) -> None:
        if self._started or self.ready:
            raise StudioError("conflict", "already started")
        try:
            for component in self.components:
                component.recover()
            for component in self.components:
                # No periodic browser worker runner is ever started or required.
                component.start()
                self._started.append(component)
            self.ready = True
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        self.ready = False
        # A still-running publisher must not lose its APSW writer while a
        # blocked remote request may eventually finish and commit state.
        # Keep failed dependencies started so shutdown can safely retry.
        while self._started:
            component=self._started[-1]
            try:
                component.stop()
            except Exception as exc:
                raise StudioError("internal", f"{component.name} failed to stop") from exc
            self._started.pop()

    def __enter__(self) -> "StudioRuntime":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
