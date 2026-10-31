"""Atomic content-addressed byte storage with project-bound read authorization."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .engine import ScopedTransaction
from .errors import Forbidden, Invalid, Missing


@dataclass(frozen=True)
class StoredBlob:
    digest: str
    byte_size: int
    store_key: str


class ContentAddressedStore:
    def __init__(self, root: str | Path):
        requested_root = Path(root)
        if requested_root.is_symlink():
            raise Invalid("blob root may not be a symlink")
        self.root = requested_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _file(self, digest: str) -> Path:
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise Invalid("content digest must be lowercase SHA-256")
        return self.root / digest[:2] / digest[2:4] / digest

    def put(self, payload: bytes) -> StoredBlob:
        if not isinstance(payload, bytes):
            raise Invalid("payload must be bytes")
        digest = hashlib.sha256(payload).hexdigest()
        dest = self._file(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.parent.is_symlink() or dest.parent.parent.is_symlink():
            raise Invalid("symlink in blob storage path")
        if dest.exists():
            self.read_digest(digest)
            return StoredBlob(digest, len(payload), str(dest.relative_to(self.root)))
        with NamedTemporaryFile(prefix=".stage-", dir=dest.parent, delete=False) as tmp:
            temp_path = Path(tmp.name)
            try:
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
        try:
            # This never overwrites an existing digest path from a competing writer.
            try:
                os.link(temp_path, dest)
            except FileExistsError:
                pass
            data = self.read_digest(digest)
            if data != payload:
                raise Invalid("hash-collision or corrupt existing blob")
            if hasattr(os, "O_DIRECTORY"):
                directory = os.open(dest.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temp_path.unlink(missing_ok=True)
        return StoredBlob(digest, len(payload), str(dest.relative_to(self.root)))

    def read_digest(self, digest: str) -> bytes:
        dest = self._file(digest)
        if dest.is_symlink() or dest.parent.is_symlink() or dest.parent.parent.is_symlink():
            raise Invalid("symlink blob rejected")
        try:
            fd = os.open(dest, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as f:
                data = f.read()
        except FileNotFoundError as exc:
            raise Missing("missing content-addressed bytes") from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise Invalid("corrupted content-addressed bytes")
        return data

    def register_verified_unscoped(self, tx: ScopedTransaction, stored: StoredBlob) -> None:
        """WG-G validates physically durable whole-Studio backup blobs.

        Never creates project_blobs access to a full multi-project database image.
        """
        if tx.domain_owner != "WG-G":
            raise Forbidden("blob registration belongs to WG-G")
        data = self.read_digest(stored.digest)
        if len(data) != stored.byte_size or str(self._file(stored.digest).relative_to(self.root)) != stored.store_key:
            raise Invalid("unverified blob metadata")
        tx._execute("INSERT INTO blobs(content_digest,byte_size,store_key,durability) VALUES (?,?,?,'verified') "
                    "ON CONFLICT(content_digest) DO NOTHING", (stored.digest, stored.byte_size, stored.store_key))

    def put_project(self, tx: ScopedTransaction, payload: bytes, *, purpose: str) -> StoredBlob:
        if tx.domain_owner != "WG-G" or not purpose:
            raise Forbidden("blob linkage is owned by WG-G")
        stored = self.put(payload)
        # Physical blob deduplication is global, but every read requires project_blobs.
        self.register_verified_unscoped(tx, stored)
        tx._execute("INSERT INTO project_blobs(project_id,content_digest,purpose) VALUES (?,?,?) "
                    "ON CONFLICT(project_id,content_digest,purpose) DO NOTHING",
                    (tx.project_id, stored.digest, purpose))
        return stored

    def read_project(self, tx: ScopedTransaction, digest: str, *, purpose: str | None = None) -> bytes:
        row = tx._execute("SELECT 1 FROM project_blobs pb JOIN blobs b ON b.content_digest=pb.content_digest "
                          "WHERE pb.project_id=? AND pb.content_digest=? AND (? IS NULL OR pb.purpose=?) "
                          "AND b.durability='verified' LIMIT 1",
                          (tx.project_id, digest, purpose, purpose)).fetchone()
        if row is None:
            raise Forbidden("no project-scoped blob reference")
        return self.read_digest(digest)
