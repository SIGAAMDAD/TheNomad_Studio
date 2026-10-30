"""Service process entrypoint; starts *configured* Studio services, not AI chats.

The factory is a trusted deployment setting supplied by integration. It must
construct project, persistence, transport and API services. No fallback
fake production backend is available.
"""
from __future__ import annotations

import importlib
import os
import signal
import sys
import threading

from studio.api.http import StudioWSGI
from studio.api.service import StudioOperatorService
from studio.api.server import create_http_server
from collections.abc import Callable

from .composition import StudioComposition
from .errors import StudioError


def load_composition(factory_path: str) -> StudioComposition:
    if not factory_path or ":" not in factory_path:
        raise StudioError("capability_unavailable", "STUDIO_COMPOSITION_FACTORY must be configured")
    module_name, func_name = factory_path.split(":", 1)
    if not module_name or not func_name.isidentifier():
        raise StudioError("invalid", "composition factory import path invalid")
    module = importlib.import_module(module_name)
    factory: Callable[[], object] = getattr(module, func_name)
    result = factory()
    if not isinstance(result, StudioComposition):
        raise StudioError("invalid", "configured composition factory returned wrong type")
    return result


def configure_operator_server(app: StudioComposition):
    """Only admit the real service when all 72 domain handlers are connected.

    API startup is intentionally a hard gate; documentation and disconnected
    domain implementations cannot count as an operational Studio.
    """
    service = app.operator_service
    if not isinstance(service, StudioOperatorService):
        raise StudioError('capability_unavailable', 'authenticated operator API not composed')
    missing = service.domains.missing(service.contracts)
    if missing:
        raise StudioError('capability_unavailable',
                          f'operator API has {len(missing)} unbound commands: '+', '.join(sorted(missing)))
    host = os.environ.get('STUDIO_OPERATOR_HOST', '127.0.0.1')
    if host not in ('127.0.0.1', '::1', 'localhost'):
        raise StudioError('forbidden', 'operator HTTP binds only to loopback; use a trusted TLS proxy')
    try:
        port = int(os.environ.get('STUDIO_OPERATOR_PORT','8000'))
    except ValueError as exc:
        raise StudioError('invalid', 'HTTP port must be an integer') from exc
    if not 1 <= port <= 65535:
        raise StudioError('invalid', 'HTTP port must be in 1..65535')
    return create_http_server(StudioWSGI(service),host=host,port=port)


def main() -> int:
    factory = os.environ.get("STUDIO_COMPOSITION_FACTORY", "")
    try:
        app = load_composition(factory)
    except (StudioError, ImportError, AttributeError, TypeError) as exc:
        print(f"Studio configuration invalid: {exc}", file=sys.stderr)
        return 78
    # A headless lifecycle-only process is not a working operator service.
    try:
        server = configure_operator_server(app)
    except (StudioError, ValueError, TypeError, OSError) as exc:
        print(f"Studio operator API unavailable: {exc}", file=sys.stderr)
        return 78
    signal_event = threading.Event()
    def shutdown(*_args: object) -> None:
        signal_event.set()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    server_faults: list[BaseException] = []
    def serve() -> None:
        try:
            server.serve_forever()
        except BaseException as exc:
            server_faults.append(exc)
            signal_event.set()
    worker = threading.Thread(target=serve, name='studio-operator-http', daemon=True)
    try:
        with app.runtime:
            worker.start()
            signal_event.wait()
    except Exception as exc:
        print(f"Studio startup/shutdown failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if worker.is_alive():
            server.shutdown()
            worker.join(timeout=5)
        server.server_close()
    if server_faults:
        print(f"Studio HTTP service stopped unexpectedly: {server_faults[0]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
