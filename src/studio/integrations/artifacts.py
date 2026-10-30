"""Project-isolated, immutable local content-addressed artifacts and format validation."""
from __future__ import annotations

import json
import os
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
    producer_session_id: str | None
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

    def _path(self, content_digest: str) -> Path:
        d = digest(content_digest)
        return self.root / self.binding.project_id / "sha256" / d[:2] / d

    @staticmethod
    def _write_once(path: Path, raw: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not path.is_file() or path.is_symlink() or path.read_bytes() != raw:
                raise IntegrationError("conflict", "content-address collision or mutation")
            return
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def put(self, data: bytes, *, project_id: str, expected_digest: str, validator: Callable[[bytes], None] | None = None) -> str:
        self.binding.require(project_id)
        if sha256(data) != digest(expected_digest):
            raise IntegrationError("invalid", "artifact content digest mismatch")
        if validator is not None:
            validator(data)
        path = self._path(expected_digest)
        self._write_once(path, data)
        return f"sha256:{expected_digest}"

    def get(self, *, project_id: str, key: str) -> bytes:
        self.binding.require(project_id)
        if not key.startswith("sha256:"):
            raise IntegrationError("invalid", "invalid content storage key")
        d = digest(key[7:])
        path = self._path(d)
        if not path.is_file() or path.is_symlink():
            raise IntegrationError("missing", "project artifact not found")
        data = path.read_bytes()
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
        if not index.is_file() or index.is_symlink():
            raise IntegrationError("missing", "project artifact not found")
        try:
            row = json.loads(index.read_bytes())
            if set(row) != set(Artifact.__dataclass_fields__) or row["project_id"] != project_id:
                raise ValueError("bad artifact metadata")
            entry = Artifact(**row)
            self.get(project_id=project_id, key=entry.storage_key)
        except (ValueError, TypeError, KeyError) as exc:
            raise IntegrationError("conflict", "artifact index invalid") from exc
        return entry

    def list_artifacts(self, *, project_id: str) -> list[dict[str, str]]:
        self.binding.require(project_id)
        d = self.root / project_id / "artifacts"
        return [self.get_artifact(project_id=project_id, artifact_id=p.stem).summary()
                for p in sorted(d.glob("*.json"))] if d.is_dir() else []
