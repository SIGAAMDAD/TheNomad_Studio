"""Private, SHA-pinned .NET ToolHost pipe transport for trusted Studio adapters.

This is a transport, not a grant or lease authority. Callers must supply a
real, current-domain authorization callback; no relay/HTTP payload can create
a broker or bypass that callback. No network listener or shell invocation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import select
import subprocess
from time import monotonic
from typing import Callable, Mapping


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[0-9a-f]{64}\Z")
_FRAME_BYTES = 65536


class ToolHostTransportError(RuntimeError):
    """Invalid local ToolHost transport, identity or protocol state."""


def _secure_file(path: Path, expected_digest: str) -> None:
    if not path.is_absolute() or not _SHA256.fullmatch(expected_digest):
        raise ToolHostTransportError("absolute file and pinned SHA-256 required")
    if not path.is_file():
        raise ToolHostTransportError("pinned ToolHost file missing")
    # Reject links for every present path component, not just the final file.
    for entry in (path, *path.parents):
        if entry.is_symlink():
            raise ToolHostTransportError("symlink in pinned ToolHost path")
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != expected_digest:
        raise ToolHostTransportError("pinned ToolHost bytes changed")


class PrivateToolHostBroker:
    """One local, private child with authorized NDJSON operation requests.

    The authorizer is the independently verified C/A/B/D lease/grant policy;
    transport never interprets worker messages as authority. A broker instance
    must be recreated after grant revocation with a new pinned registry.
    """

    def __init__(
        self, *, dotnet: Path, host_dll: Path, host_sha256: str,
        registry: Path, registry_sha256: str, journal: Path,
        authorize: Callable[[Mapping[str, object]], bool],
        expected_isolation: str | None = None, timeout: float = 10.0,
    ):
        if os.name != "posix":
            raise ToolHostTransportError("POSIX private-pipe transport required")
        if not callable(authorize):
            raise ToolHostTransportError("trusted current-operation authorizer required")
        if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not 0.25 <= timeout <= 120):
            raise ToolHostTransportError("invalid transport timeout")
        self.dotnet = Path(dotnet)
        self.host_dll = Path(host_dll)
        self.host_sha256 = host_sha256
        self.registry = Path(registry)
        self.registry_sha256 = registry_sha256
        self.journal = Path(journal)
        self.authorize = authorize
        self.expected_isolation = expected_isolation
        self.timeout = float(timeout)
        self._child: subprocess.Popen[bytes] | None = None
        self._buffer = bytearray()
        self._authorized: set[tuple[str, str]] = set()
        self.supported_isolation: tuple[str, ...] = ()

    @property
    def running(self) -> bool:
        return self._child is not None and self._child.poll() is None

    def _verify_preflight(self) -> None:
        if not self.dotnet.is_absolute() or not self.dotnet.is_file():
            raise ToolHostTransportError("absolute installed dotnet launcher required")
        _secure_file(self.host_dll, self.host_sha256)
        _secure_file(self.registry, self.registry_sha256)
        if not self.journal.is_absolute() or self.journal.is_symlink():
            raise ToolHostTransportError("private absolute journal required")
        self.journal.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(part.is_symlink() for part in (self.journal, *self.journal.parents)):
            raise ToolHostTransportError("journal path contains a symlink")

    def start(self) -> "PrivateToolHostBroker":
        if self._child is not None:
            raise ToolHostTransportError("ToolHost transport already started")
        self._verify_preflight()
        args = (str(self.dotnet), str(self.host_dll), "--registry",
                str(self.registry), "--registry-sha256",
                self.registry_sha256, "--journal", str(self.journal))
        try:
            self._child = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, close_fds=True, shell=False,
                cwd=str(self.journal))
            challenge = self._read_frame()
            if (set(challenge) != {
                    "kind", "protocol_version", "host_nonce", "supported_isolation"}
                    or challenge["kind"] != "handshake"
                    or challenge["protocol_version"] != "1.0.0"
                    or not isinstance(challenge["host_nonce"], str)
                    or not _NONCE.fullmatch(challenge["host_nonce"])
                    or not isinstance(challenge["supported_isolation"], list)
                    or any(not isinstance(item, str) or not item
                           for item in challenge["supported_isolation"])):
                raise ToolHostTransportError("untrusted ToolHost handshake")
            self.supported_isolation = tuple(challenge["supported_isolation"])
            if (self.expected_isolation is not None
                    and self.expected_isolation not in self.supported_isolation):
                raise ToolHostTransportError("required host sandbox unavailable")
            self._write_frame(challenge)
            return self
        except BaseException:
            self.close()
            raise

    def _write_frame(self, value: Mapping[str, object]) -> None:
        if not self.running or self._child is None or self._child.stdin is None:
            raise ToolHostTransportError("ToolHost is not running")
        try:
            line = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ToolHostTransportError("invalid ToolHost request frame") from exc
        if not line or len(line) > _FRAME_BYTES or b"\n" in line:
            raise ToolHostTransportError("ToolHost request exceeds frame bound")
        try:
            self._child.stdin.write(line + b"\n")
            self._child.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ToolHostTransportError("ToolHost pipe closed") from exc

    def _read_frame(self) -> dict:
        if self._child is None or self._child.stdout is None:
            raise ToolHostTransportError("ToolHost transport not started")
        deadline = monotonic() + self.timeout
        while True:
            pos = self._buffer.find(b"\n")
            if pos >= 0:
                payload = bytes(self._buffer[:pos])
                del self._buffer[:pos + 1]
                if not payload or len(payload) > _FRAME_BYTES:
                    raise ToolHostTransportError("invalid ToolHost frame size")
                try:
                    data = json.loads(payload.decode("utf-8"))
                except (UnicodeError, ValueError) as exc:
                    raise ToolHostTransportError("invalid ToolHost response JSON") from exc
                if not isinstance(data, dict):
                    raise ToolHostTransportError("ToolHost response is not an object")
                return data
            if len(self._buffer) > _FRAME_BYTES:
                raise ToolHostTransportError("ToolHost response exceeds frame bound")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ToolHostTransportError("ToolHost response timeout")
            readable, _, _ = select.select([self._child.stdout], [], [], remaining)
            if not readable:
                raise ToolHostTransportError("ToolHost response timeout")
            chunk = os.read(self._child.stdout.fileno(), 4096)
            if not chunk:
                raise ToolHostTransportError("ToolHost closed without response")
            self._buffer.extend(chunk)

    def send(self, frame: Mapping[str, object]) -> None:
        if not isinstance(frame, Mapping) or frame.get("kind") not in {
                "run", "observe", "cancel"}:
            raise ToolHostTransportError("unsupported ToolHost command")
        if frame.get("protocol_version") != "1.0.0":
            raise ToolHostTransportError("unsupported ToolHost protocol")
        project, operation = frame.get("project_id"), frame.get("operation_id")
        if not isinstance(project, str) or not project or not isinstance(operation, str) or not operation:
            raise ToolHostTransportError("unscoped ToolHost operation")
        if frame["kind"] == "run" and not self.expected_isolation:
            raise ToolHostTransportError("tool execution requires pinned sandbox isolation")
        try:
            allowed = self.authorize(frame)
        except Exception as exc:
            raise ToolHostTransportError("current ToolHost authorization unavailable") from exc
        if allowed is not True:
            raise ToolHostTransportError("current ToolHost authorization denied")
        self._write_frame(frame)
        self._authorized.add((project, operation))

    def receive(self) -> dict:
        frame = self._read_frame()
        if (frame.get("kind") != "receipt" or frame.get("protocol_version") != "1.0.0"
                or (frame.get("project_id"), frame.get("operation_id")) not in self._authorized
                or frame.get("state") not in {
                    "queued", "running", "completed", "failed", "canceled", "unknown"}):
            raise ToolHostTransportError("unexpected or unbound ToolHost receipt")
        return frame

    def close(self) -> None:
        child, self._child = self._child, None
        self._buffer.clear()
        self._authorized.clear()
        if child is None:
            return
        try:
            if child.stdin:
                child.stdin.close()
            try:
                child.wait(timeout=min(self.timeout, 5.0))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5.0)
        finally:
            if child.stdout:
                child.stdout.close()

    def __enter__(self) -> "PrivateToolHostBroker":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()
