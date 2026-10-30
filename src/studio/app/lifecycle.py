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
        errors: list[BaseException] = []
        for component in reversed(self._started):
            try:
                component.stop()
            except Exception as exc:
                errors.append(exc)
        self._started.clear()
        if errors:
            raise StudioError("internal", f"{len(errors)} component(s) failed to stop") from errors[0]

    def __enter__(self) -> "StudioRuntime":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
