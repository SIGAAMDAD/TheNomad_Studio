"""Verify installed project capability package resources; executor remains WG-H."""
from __future__ import annotations

import json
from pathlib import Path
from .common import IntegrationError, ProjectBinding, digest, sha256


class PackageVerifier:
    def __init__(self, binding: ProjectBinding):
        self.binding = binding

    def verify(self, *, project_id: str, root: Path, manifest_bytes: bytes, expected_digest: str) -> dict:
        self.binding.require(project_id)
        if sha256(manifest_bytes) != digest(expected_digest):
            raise IntegrationError("conflict", "package manifest digest differs from installed lock")
        try:
            manifest = json.loads(manifest_bytes)
        except (UnicodeError, ValueError) as exc:
            raise IntegrationError("invalid", "invalid package manifest") from exc
        if not isinstance(manifest, dict) or manifest.get("manifest_version") != "1":
            raise IntegrationError("invalid", "package schema mismatch")
        if not isinstance(manifest.get("resources"), list) or not isinstance(manifest.get("entrypoints"), list):
            raise IntegrationError("invalid", "package schema missing resources/entrypoints")
        root = Path(root).resolve()
        resource_paths = set()
        for item in manifest["resources"]:
            if not isinstance(item, dict) or set(item) != {"path", "sha256", "byte_size"}:
                raise IntegrationError("invalid", "package resource schema invalid")
            relative = Path(item["path"])
            path = (root / relative).resolve()
            if relative.is_absolute() or not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
                raise IntegrationError("invalid", "package resource escapes installed root or missing")
            if path.stat().st_size != item["byte_size"] or sha256(path.read_bytes()) != digest(item["sha256"]):
                raise IntegrationError("conflict", "package resource failed byte pin")
            if item["path"] in resource_paths:
                raise IntegrationError("invalid", "duplicate package resource")
            resource_paths.add(item["path"])
        for ep in manifest["entrypoints"]:
            if not all(ep.get(field) in resource_paths for field in ("executable_path", "args_schema_path", "output_schema_path")):
                raise IntegrationError("invalid", "entrypoint references unpinned resources")
            for name, filename in (("executable_digest", "executable_path"),
                                   ("args_schema_digest", "args_schema_path"),
                                   ("output_schema_digest", "output_schema_path")):
                entry = next(item for item in manifest["resources"] if item["path"] == ep[filename])
                if ep[name] != entry["sha256"]:
                    raise IntegrationError("invalid", "entrypoint digest differs from resource manifest")
        return manifest
