"""Verified WHOLE-STUDIO SQLite + blob snapshots and fresh-target restoration.

Never expose a whole-Studio database image via a per-project artifact read.
Project-only live restore/promotion is WG-M's separately authorized operation.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import time_ns
from typing import Any

from .blobs import ContentAddressedStore
from .engine import ScopedStore, ScopedTransaction
from .errors import Conflict, Forbidden, Invalid, Missing, Unavailable
from .idempotency import canonical_json_bytes
from .schema import schema_bytes, ensure_existing_database


@dataclass(frozen=True)
class BackupDescriptor:
    backup_digest: str
    database_blob_digest: str
    manifest_digest: str
    schema_digest: str
    blob_count: int


class BackupManager:
    """Offline recovery image includes every project and every verified blob."""

    def __init__(self, blob_store: ContentAddressedStore):
        self._blobs = blob_store

    def create_whole_studio(self, store: ScopedStore) -> BackupDescriptor:
        """APSW online backup; snapshot DB once and bind every physical blob hash."""
        try:
            import apsw
        except ImportError as exc:
            raise Unavailable("APSW required for online snapshot") from exc
        with store._mutex:
            if not store._conn.getautocommit():
                raise Conflict("cannot snapshot during an open write transaction")
            with NamedTemporaryFile(prefix="studio-backup-", suffix=".sqlite", delete=False) as f:
                tmp = Path(f.name)
            try:
                destination = apsw.Connection(str(tmp))
                try:
                    with destination.backup("main", store._conn, "main") as backup:
                        while not backup.done:
                            backup.step(250)
                    if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise Invalid("backup failed SQLite integrity check")
                finally:
                    destination.close()
                db_bytes = tmp.read_bytes()
                verified_blobs = list(store._conn.execute("SELECT content_digest,byte_size FROM blobs WHERE durability='verified' ORDER BY content_digest"))
                for digest, size in verified_blobs:
                    raw = self._blobs.read_digest(digest)
                    if len(raw) != size:
                        raise Invalid("backup blob inventory size mismatch")
                db_stored = self._blobs.put(db_bytes)
                manifest = {
                    "format": "nomad-studio-r5-whole-studio-backup",
                    "schema_sha256": hashlib.sha256(schema_bytes()).hexdigest(),
                    "database_sha256": db_stored.digest,
                    "database_size": len(db_bytes),
                    "blobs": [{"digest": d, "size": s} for d, s in verified_blobs],
                }
                manifest_stored = self._blobs.put(canonical_json_bytes(manifest))
                backup_digest = hashlib.sha256((db_stored.digest + ":" + manifest_stored.digest).encode()).hexdigest()
                return BackupDescriptor(backup_digest, db_stored.digest, manifest_stored.digest,
                                        manifest["schema_sha256"], len(verified_blobs))
            finally:
                tmp.unlink(missing_ok=True)

    def register_verified(self, tx: ScopedTransaction, descriptor: BackupDescriptor, *, created_at: str) -> None:
        """C records an already independently checked image. G owns blob metadata."""
        if tx.domain_owner != "WG-C":
            raise Forbidden("only WG-C can register backup evidence")
        self.verify_sqlite_image(descriptor)
        for digest in (descriptor.database_blob_digest, descriptor.manifest_digest):
            if tx._execute("SELECT 1 FROM blobs WHERE content_digest=? AND durability='verified'", (digest,)).fetchone() is None:
                raise Missing("verified backup bytes must be registered by WG-G")
            self._blobs.read_digest(digest)
        tx._execute("INSERT INTO backup_sets(project_id,backup_digest,database_blob_digest,manifest_digest,schema_digest,state,created_at) "
                    "VALUES (?,?,?,?,?,'verified',?)",
                    (tx.project_id, descriptor.backup_digest, descriptor.database_blob_digest,
                     descriptor.manifest_digest, descriptor.schema_digest, created_at))

    def verify(self, descriptor: BackupDescriptor) -> dict[str, Any]:
        manifest = json.loads(self._blobs.read_digest(descriptor.manifest_digest))
        if manifest.get("format") != "nomad-studio-r5-whole-studio-backup":
            raise Invalid("backup manifest format mismatch")
        if manifest.get("schema_sha256") != hashlib.sha256(schema_bytes()).hexdigest():
            raise Invalid("backup frozen schema mismatch")
        if manifest.get("database_sha256") != descriptor.database_blob_digest or manifest.get("schema_sha256") != descriptor.schema_digest:
            raise Invalid("backup descriptor mismatch")
        if descriptor.backup_digest != hashlib.sha256((descriptor.database_blob_digest + ":" + descriptor.manifest_digest).encode()).hexdigest():
            raise Invalid("backup identity mismatch")
        db = self._blobs.read_digest(descriptor.database_blob_digest)
        if len(db) != manifest.get("database_size"):
            raise Invalid("database image size mismatch")
        inventory = manifest.get("blobs")
        if not isinstance(inventory, list) or len(inventory) != descriptor.blob_count:
            raise Invalid("inventory cardinality mismatch")
        for item in inventory:
            if not isinstance(item, dict) or len(self._blobs.read_digest(item["digest"])) != item["size"]:
                raise Invalid("backup inventory corrupted")
        return manifest

    def verify_sqlite_image(self, descriptor: BackupDescriptor) -> None:
        """Actual APSW integrity and FK proof before accepting a verified backup."""
        try:
            import apsw
        except ImportError as exc:
            raise Unavailable("APSW required to verify SQLite backup bytes") from exc
        manifest = self.verify(descriptor)
        db_bytes = self._blobs.read_digest(descriptor.database_blob_digest)
        with NamedTemporaryFile(prefix=".backup-verify-", suffix=".sqlite", delete=False) as f:
            path = Path(f.name)
            f.write(db_bytes)
            f.flush()
            os.fsync(f.fileno())
        try:
            source = apsw.Connection(str(path), flags=apsw.SQLITE_OPEN_READONLY)
            try:
                source.execute("PRAGMA foreign_keys=ON")
                ensure_existing_database(source)
                if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise Invalid("backup SQLite integrity check failed")
                if list(source.execute("PRAGMA foreign_key_check")):
                    raise Invalid("backup SQLite foreign-key check failed")
                rows = list(source.execute("SELECT content_digest,byte_size FROM blobs WHERE durability='verified' ORDER BY content_digest"))
                if [{"digest": d, "size": n} for d, n in rows] != manifest["blobs"]:
                    raise Invalid("backup SQLite inventory disagrees with bytes")
            finally:
                source.close()
        finally:
            path.unlink(missing_ok=True)

    def restore_to_fresh_database(self, descriptor: BackupDescriptor, destination: str | Path,
                                  *, recovery_log: str | Path) -> str:
        """Restores a complete multi-project image ONLY to a nonexistent DB path.

        This method cannot replace the running store or implicitly overwrite any
        other project's state. WG-M owns authorization to promote a recovered image.
        """
        try:
            import apsw
        except ImportError as exc:
            raise Unavailable("APSW required for verified restore") from exc
        dest = Path(destination)
        if dest.exists() or dest.is_symlink() or not dest.parent.exists():
            raise Conflict("restore destination must be a new database in an existing directory")
        manifest = self.verify(descriptor)
        db_bytes = self._blobs.read_digest(descriptor.database_blob_digest)
        with NamedTemporaryFile(prefix=".restore-", suffix=".sqlite", dir=dest.parent, delete=False) as f:
            tmp = Path(f.name)
            f.write(db_bytes)
            f.flush()
            os.fsync(f.fileno())
        try:
            probe = apsw.Connection(str(tmp), flags=apsw.SQLITE_OPEN_READONLY)
            try:
                probe.execute("PRAGMA foreign_keys=ON")
                ensure_existing_database(probe)
                if probe.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise Invalid("restored SQLite file failed quick_check")
                if list(probe.execute("PRAGMA foreign_key_check")):
                    raise Invalid("restored SQLite file violates referential integrity")
                registered = list(probe.execute("SELECT content_digest,byte_size FROM blobs WHERE durability='verified' ORDER BY content_digest"))
                if [{"digest": d, "size": s} for d, s in registered] != manifest["blobs"]:
                    raise Invalid("restored blob inventory does not match SQLite records")
            finally:
                probe.close()
            try:
                os.link(tmp, dest)  # atomic create-if-absent, no silent replace
            except FileExistsError as exc:
                raise Conflict("another restore created the destination") from exc
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            if digest != descriptor.database_blob_digest:
                dest.unlink(missing_ok=True)
                raise Invalid("restored database byte hash mismatch")
            receipt = {
                "backup_digest": descriptor.backup_digest,
                "schema_digest": descriptor.schema_digest,
                "restored_database_sha256": digest,
                "destination": str(dest),
                "verified_blobs": descriptor.blob_count,
                "event": "fresh_whole_studio_restore_verified",
                "clock_ns": time_ns(),
            }
            log_path = Path(recovery_log)
            if log_path.is_symlink():
                raise Invalid("recovery log may not be symlink")
            try:
                with log_path.open("ab") as log:
                    log.write(canonical_json_bytes(receipt) + b"\n")
                    log.flush()
                    os.fsync(log.fileno())
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
            return hashlib.sha256(canonical_json_bytes(receipt)).hexdigest()
        finally:
            tmp.unlink(missing_ok=True)
