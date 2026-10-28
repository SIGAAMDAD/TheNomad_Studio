"""Mutual-TLS JSON gateway transport. No owner connector or bearer fallback.

A trusted deployer passes an initialized Gateway with isolated PG and provider
adapters; absent those adapters, requests deny. The listener requires client
certificates from the configured CA AND explicit leaf fingerprint allowlisting.
"""
from __future__ import annotations

import base64
import hashlib
import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping

from .gateway import Gateway
from .interfaces import Principal

MAX_BODY = 18 * 1024 * 1024  # bounded artifact base64 + JSON


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-finite JSON value')


class MTLSIdentity:
    def __init__(self, fingerprints: Mapping[str, str]):
        if not fingerprints or any(len(k) != 64 or not v for k, v in fingerprints.items()):
            raise ValueError("Explicit client certificate fingerprint map required")
        self.fingerprints = dict(fingerprints)

    def authenticate(self, connection) -> Principal | None:
        if not isinstance(connection, ssl.SSLSocket):
            return None
        # OpenSSL has verified CA chain via CERT_REQUIRED in server SSLContext.
        der = connection.getpeercert(binary_form=True)
        if not der:
            return None
        principal = self.fingerprints.get(hashlib.sha256(der).hexdigest())
        return Principal(principal) if principal else None


def server_context(*, certfile: str, keyfile: str, cafile: str) -> ssl.SSLContext:
    if not certfile or not keyfile or not cafile:
        raise ValueError("Server certificate, key and trusted client CA required")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_cert_chain(certfile, keyfile)
    ctx.load_verify_locations(cafile)
    return ctx


def make_handler(gateway: Gateway, identity: MTLSIdentity, *, recovery=None, health=None, monitor_principals=()):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NomadGateway/1"
        sys_version = ""

        def log_message(self, format, *args):
            # No URLs with query strings, auth headers or bodies in logs.
            pass

        def do_GET(self):
            if self.path != '/v2/health' or health is None:
                self.send_error(405, "POST required")
                return
            principal = identity.authenticate(self.connection)
            if principal is None or principal.principal_id not in monitor_principals:
                self.send_error(403)
                return
            try:
                result = health()
                raw = json.dumps(result, separators=(',', ':')).encode()
                if len(raw) > 16384:
                    self.send_error(503)
                    return
            except Exception:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            recovery_route = self.path == '/internal/v2/publish/reconcile' and recovery is not None
            if not recovery_route and self.path not in ("/v1/auth/evaluate", "/v1/publish/prepare", "/v1/publish/reconcile"):
                self.send_error(404)
                return
            principal = identity.authenticate(self.connection)
            if principal is None:
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 1 or length > MAX_BODY or self.headers.get("Transfer-Encoding"):
                    self.send_error(413)
                    return
                payload = json.loads(self.rfile.read(length), object_pairs_hook=_unique_fields,
                                     parse_constant=_invalid_constant)
                if not isinstance(payload, dict):
                    self.send_error(400)
                    return
                if recovery_route:
                    if set(payload) != {'publication_id', 'idempotency_key'}:
                        self.send_error(400)
                        return
                    result = recovery.reconcile(payload['publication_id'], payload['idempotency_key'], principal)
                elif self.path == "/v1/auth/evaluate":
                    result = gateway.evaluate(payload, principal)
                elif self.path == "/v1/publish/prepare":
                    encoded = payload.get("artifact_base64")
                    if not isinstance(encoded, str):
                        self.send_error(400)
                        return
                    artifact = base64.b64decode(encoded, validate=True)
                    result = gateway.prepare(payload.get("intent"), artifact, principal)
                else:
                    result = gateway.reconcile(payload.get("publication_id", "invalid-request"),
                                               payload.get("idempotency_key", "0" * 64), principal)
            except (ValueError, TypeError, UnicodeError, OverflowError):
                self.send_error(400)
                return
            except Exception:
                # Never send provider or trusted authority exceptions to a caller.
                self.send_error(503)
                return
            raw = json.dumps(result, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
    return Handler


def serve(gateway: Gateway, identities: MTLSIdentity, tls: ssl.SSLContext,
          host: str = "127.0.0.1", port: int = 8443, *, recovery=None,
          health=None, monitor_principals=()) -> None:
    if tls.verify_mode != ssl.CERT_REQUIRED:
        raise ValueError("Mutual TLS required")
    server = ThreadingHTTPServer((host, port), make_handler(gateway, identities, recovery=recovery, health=health,
                                                                   monitor_principals=monitor_principals))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
