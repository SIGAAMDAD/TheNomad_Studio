"""Service process entrypoint; starts *configured* Studio services, not AI chats.

The factory is a trusted deployment setting supplied by integration. It must
construct WG-C/G/I/J-backed services and return MComposition. No fallback
fake production backend is available.
"""
from __future__ import annotations

import importlib
import os
import signal
import sys
import threading
from collections.abc import Callable

from .composition import MComposition
from .errors import StudioError


def load_composition(factory_path: str) -> MComposition:
    if not factory_path or ":" not in factory_path:
        raise StudioError("capability_unavailable", "STUDIO_COMPOSITION_FACTORY must be configured")
    module_name, func_name = factory_path.split(":", 1)
    if not module_name or not func_name.isidentifier():
        raise StudioError("invalid", "composition factory import path invalid")
    module = importlib.import_module(module_name)
    factory: Callable[[], object] = getattr(module, func_name)
    result = factory()
    if not isinstance(result, MComposition):
        raise StudioError("invalid", "configured composition factory returned wrong type")
    return result


def main() -> int:
    factory = os.environ.get("STUDIO_COMPOSITION_FACTORY", "")
    try:
        app = load_composition(factory)
    except (StudioError, ImportError, AttributeError, TypeError) as exc:
        print(f"Studio configuration invalid: {exc}", file=sys.stderr)
        return 78
    signal_event = threading.Event()
    def shutdown(*_args: object) -> None:
        signal_event.set()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        with app.runtime:
            signal_event.wait()
    except Exception as exc:
        print(f"Studio startup/shutdown failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
