"""Project-isolated, immutable content-addressed artifacts.

All file operations traverse directories relative to an opened root descriptor.
Symlinked directories, links at the final path, and non-regular files are
rejected so another local user cannot redirect a verified blob into a different
project or outside the configured artifact store.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .common import IntegrationError, ProjectBinding, canonical, digest, identifier, sha256


@dataclass(frozen=True)
class Artifact:
    project_id: str
    artifact_id: str
    kind: str
    content_digest: str
    manifest_digest: str
    producer_shift_id: str | None
    source_revision_id: str | None
    canon_snapshot_id: str | None
    validator_digest: str | None
    storage_key: str

    def summary(self) -> dict[str, str]:
        return {"artifact_id": self.artifact_id, "kind": self.kind, "content_digest": self.content_digest}


class ArtifactStore:
    def __init__(self, root: Path, binding: ProjectBinding):
        self.root = Path(root).resolve()
        self.binding = binding
        if not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY")) or os.open not in os.supports_dir_fd:
            raise IntegrationError("capability_unavailable", "artifact storage requires no-follow directory handles")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _path(self, content_digest: str) -> Path:
        d = digest(content_digest)
        return self.root / self.binding.project_id / "sha256" / d[:2] / d

    def _dir(self, path: Path, *, create: bool = False) -> int:
        """Open a directory beneath root, rejecting links at every component."""
        try:
            parts = path.relative_to(self.root).parts
        except ValueError as exc:
            raise IntegrationError("forbidden", "artifact path escapes root") from exc
        if any(part in (".", "..", "") for part in parts):
            raise IntegrationError("invalid", "unsafe artifact path")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = -1
        try:
            current = os.open(self.root, flags)
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                next_fd = os.open(part, flags, dir_fd=current)
                os.close(current)
                current = next_fd
            fd, current = current, -1
            return fd
        except FileNotFoundError as exc:
            raise IntegrationError("missing", "artifact directory not found") from exc
        except OSError as exc:
            raise IntegrationError("conflict", "artifact directory is unavailable or unsafe") from exc
        finally:
            if current >= 0:
                os.close(current)

    def _write_once(self, path: Path, raw: bytes) -> None:
        """Publish a complete, fsynced blob atomically; no partial final files."""
        parent = self._dir(path.parent, create=True)
        temporary = "." + path.name + ".staging-" + secrets.token_hex(12)
        fd = -1
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            with os.fdopen(fd, "wb", closefd=True) as output:
                fd = -1
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, path.name, src_dir_fd=parent,
                        dst_dir_fd=parent, follow_symlinks=False)
            except FileExistsError:
                existing_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    if not stat.S_ISREG(os.fstat(existing_fd).st_mode):
                        raise IntegrationError("conflict", "artifact path is not a regular file")
                    with os.fdopen(existing_fd, "rb", closefd=False) as existing:
                        if existing.read() != raw:
                            raise IntegrationError("conflict", "content-address collision or mutation")
                finally:
                    os.close(existing_fd)
            os.fsync(parent)
        except OSError as exc:
            raise IntegrationError("conflict", "artifact publication failed or encountered an unsafe link") from exc
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    def _read(self, path: Path) -> bytes:
        parent = self._dir(path.parent)
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise IntegrationError("conflict", "artifact path is not a regular file")
                with os.fdopen(fd, "rb", closefd=False) as source:
                    return source.read()
            finally:
                os.close(fd)
        except FileNotFoundError as exc:
            raise IntegrationError("missing", "project artifact not found") from exc
        except OSError as exc:
            raise IntegrationError("conflict", "unsafe artifact path") from exc
        finally:
            os.close(parent)

    def put(self, data: bytes, *, project_id: str, expected_digest: str, validator: Callable[[bytes], None] | None = None) -> str:
        self.binding.require(project_id)
        if sha256(data) != digest(expected_digest):
            raise IntegrationError("invalid", "artifact content digest mismatch")
        if validator is not None:
            validator(data)
        self._write_once(self._path(expected_digest), data)
        return f"sha256:{expected_digest}"

    def get(self, *, project_id: str, key: str) -> bytes:
        self.binding.require(project_id)
        if not key.startswith("sha256:"):
            raise IntegrationError("invalid", "invalid content storage key")
        d = digest(key[7:])
        data = self._read(self._path(d))
        if sha256(data) != d:
            raise IntegrationError("conflict", "stored content failed digest verification")
        return data

    def register(self, meta: Artifact, *, validator: Callable[[bytes], None] | None = None) -> None:
        self.binding.require(meta.project_id)
        identifier(meta.artifact_id)
        digest(meta.manifest_digest)
        if meta.validator_digest is not None:
            digest(meta.validator_digest)
        if meta.storage_key != f"sha256:{digest(meta.content_digest)}":
            raise IntegrationError("invalid", "artifact storage/content mismatch")
        payload = self.get(project_id=meta.project_id, key=meta.storage_key)
        # The manifest is an independently content-addressed original; a hash
        # supplied as metadata without stored bytes is not a verified artifact.
        self.get(project_id=meta.project_id, key='sha256:' + meta.manifest_digest)
        if meta.validator_digest is not None and validator is None:
            raise IntegrationError("invalid", "declared format validator must actually execute")
        if validator is not None:
            validator(payload)
        record = canonical(vars(meta))
        index = self.root / meta.project_id / "artifacts" / (meta.artifact_id + ".json")
        self._write_once(index, record)

    def get_artifact(self, *, project_id: str, artifact_id: str) -> Artifact:
        self.binding.require(project_id)
        index = self.root / project_id / "artifacts" / (identifier(artifact_id) + ".json")
        try:
            row = json.loads(self._read(index))
            if set(row) != set(Artifact.__dataclass_fields__) or row["project_id"] != project_id:
                raise ValueError("bad artifact metadata")
            entry = Artifact(**row)
            self.get(project_id=project_id, key=entry.storage_key)
            self.get(project_id=project_id, key='sha256:' + digest(entry.manifest_digest))
        except (ValueError, TypeError, KeyError, IntegrationError) as exc:
            raise IntegrationError("conflict", "artifact index invalid") from exc
        return entry

    def list_artifacts(self, *, project_id: str) -> list[dict[str, str]]:
        self.binding.require(project_id)
        try:
            directory = self._dir(self.root / project_id / "artifacts")
        except IntegrationError as exc:
            if exc.code == "missing":
                return []
            raise
        try:
            names = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
        finally:
            os.close(directory)
        return [self.get_artifact(project_id=project_id, artifact_id=name[:-5]).summary() for name in names]
