"""HTTP host wiring for a *fully injected* authenticated StudioWSGI instance.

WG-M owns production process orchestration, TLS/front proxy and dependency
configuration. This module runs a working stdlib HTTP host for local tests
and embedding; it never installs placeholder domain backends or fake grants.
"""
from __future__ import annotations

from wsgiref.simple_server import WSGIServer, make_server

from .http import StudioWSGI


def create_http_server(app: StudioWSGI, host: str = '127.0.0.1',
                       port: int = 0) -> WSGIServer:
    if not isinstance(app, StudioWSGI):
        raise TypeError('authenticated StudioWSGI application required')
    return make_server(host, port, app)
