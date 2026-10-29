"""Offline immutable compressed SQLite archive format (NOMAD/ARCHIVE/1).

This code only opens offline copies. It never connects to the live authority.
It does not interpret or execute archived source material.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Iterable, Mapping

VERSION = "NOMAD/ARCHIVE/1"
CHUNK = 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024 * 1024  # decompressed SQLite artifact
MAX_ENTRIES = 10_000


class ArchiveError(ValueError):
    """An archive or archived input has failed verification."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def integrity_check(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 100:
        raise ArchiveError("missing or invalid SQLite file")
    with path.open("rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise ArchiveError("not a SQLite database")
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        conn.enable_load_extension(False)
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        if rows != [("ok",)]:
            raise ArchiveError("SQLite integrity_check failed")
    except sqlite3.DatabaseError as e:
        raise ArchiveError("invalid SQLite database") from e
    finally:
        conn.close()


def _insert_shards(conn: sqlite3.Connection, entry_id: int, stream: Iterable[bytes]) -> tuple[int, str, list[str]]:
    """Stream content into offline SQLite; memory bounded by source block size."""
    sha = hashlib.sha256()
    total = 0
    hashes: list[str] = []
    for i, block in enumerate(stream):
        if not isinstance(block, bytes) or not 0 < len(block) <= CHUNK:
            raise ArchiveError("invalid shard length")
        total += len(block)
        if total > MAX_ARCHIVE_BYTES:
            raise ArchiveError("entry exceeds archive size limit")
        block_sha = digest(block)
        hashes.append(block_sha)
        sha.update(block)
        conn.execute("INSERT INTO shards VALUES (?,?,?,?)", (entry_id, i, block_sha, block))
    return total, sha.hexdigest(), hashes


def _byte_blocks(data: bytes) -> Iterable[bytes]:
    for i in range(0, len(data), CHUNK):
        yield data[i:i + CHUNK]


def _file_blocks(path: Path) -> Iterable[bytes]:
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            yield block


def build_database(snapshot: Path, records: list[Mapping[str, Any]], output: Path, *, archive_id: str) -> dict[str, Any]:
    """Build an offline archival database; input records contain authenticated bytes.

    records: [{reference, revision, kind, data: bytes}]. No source executed.
    """
    integrity_check(snapshot)
    if len(records) > MAX_ENTRIES - 1:
        raise ArchiveError("too many records")
    keys = [(r["kind"], r["reference"], r["revision"]) for r in records]
    if len(keys) != len(set(keys)):
        raise ArchiveError("duplicate evidence reference")
    ordered = sorted(records, key=lambda r: (r["kind"], r["reference"], r["revision"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()  # private work file, never a sealed archive
    conn = sqlite3.connect(output)
    try:
        output.chmod(0o600)
        conn.execute("PRAGMA page_size=4096")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.executescript("""
            CREATE TABLE header (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
            CREATE TABLE entries (
                entry_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, reference TEXT NOT NULL,
                revision TEXT NOT NULL, byte_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL, shard_count INTEGER NOT NULL,
                UNIQUE (kind, reference, revision)
            );
            CREATE TABLE shards (
                entry_id INTEGER NOT NULL, ordinal INTEGER NOT NULL,
                sha256 TEXT NOT NULL, payload BLOB NOT NULL,
                PRIMARY KEY(entry_id, ordinal), FOREIGN KEY(entry_id) REFERENCES entries(entry_id)
            ) WITHOUT ROWID;
        """)
        entries: list[dict[str, Any]] = []
        inputs = [("database", "sqlite-authority", "snapshot", _file_blocks(snapshot), None)]
        for r in ordered:
            data = r["data"]
            if not isinstance(data, bytes):
                raise ArchiveError("evidence must be immutable bytes")
            inputs.append((r["kind"], r["reference"], r["revision"], _byte_blocks(data), r.get("sha256")))
        for kind, reference, revision, blocks, expected_sha in inputs:
            cur = conn.execute("INSERT INTO entries(kind, reference, revision, byte_size, sha256, shard_count) VALUES (?,?,?,?,?,?)",
                               (kind, reference, revision, 1, "pending", 0))
            entry_id = cur.lastrowid
            size, sha, hashes = _insert_shards(conn, entry_id, blocks)
            if size == 0:
                raise ArchiveError("empty archived record")
            if expected_sha is not None and expected_sha != sha:
                raise ArchiveError("historical source SHA does not match its approved digest")
            conn.execute("UPDATE entries SET byte_size=?, sha256=?, shard_count=? WHERE entry_id=?",
                         (size, sha, len(hashes), entry_id))
            entries.append({"kind": kind, "reference": reference, "revision": revision,
                            "byte_size": size, "sha256": sha, "shards": hashes})
        manifest = {"version": VERSION, "archive_id": archive_id, "entries": entries}
        raw = canonical(manifest)
        conn.execute("INSERT INTO header VALUES (?,?)", ("manifest", raw.decode("ascii")))
        conn.execute("INSERT INTO header VALUES (?,?)", ("manifest_sha256", digest(raw)))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    integrity_check(output)
    return manifest


def compress_database(db: Path, part: Path, *, fail_after_bytes: int | None = None) -> str:
    """Write private unfinished data; no final path is ever published here."""
    part.parent.mkdir(parents=True, exist_ok=True)
    with db.open("rb") as source, part.open("wb") as raw:
        os.fchmod(raw.fileno(), 0o600)
        with gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=raw, mtime=0) as dest:
            written = 0
            while block := source.read(CHUNK):
                dest.write(block)
                written += len(block)
                if fail_after_bytes is not None and written >= fail_after_bytes:
                    raise RuntimeError("injected compression interruption")
        raw.flush()
        os.fsync(raw.fileno())
    fsync_dir(part.parent)
    return file_digest(part)


def _decompress(path: Path, destination: Path, max_bytes: int) -> None:
    count = 0
    try:
        with gzip.open(path, "rb") as src, destination.open("xb") as out:
            while block := src.read(CHUNK):
                count += len(block)
                if count > max_bytes:
                    raise ArchiveError("archive decompression exceeds bound")
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
    except (OSError, EOFError, zlib_error()) as e:
        raise ArchiveError("corrupted or truncated compression stream") from e


def zlib_error():
    import zlib
    return zlib.error


def inspect_archive(path: Path, *, restore_snapshot_to: Path | None = None,
                    max_bytes: int = MAX_ARCHIVE_BYTES) -> dict[str, Any]:
    """Independently decompress, integrity-check, and hash every stored shard.

    Optional restore writes *only* to caller-selected offline path. Never cuts over.
    """
    if max_bytes <= 0 or max_bytes > MAX_ARCHIVE_BYTES:
        raise ArchiveError("invalid decompression bound")
    if not path.is_file() or path.stat().st_size == 0:
        raise ArchiveError("compressed archive missing")
    if restore_snapshot_to is not None:
        if restore_snapshot_to.exists():
            raise ArchiveError("refusing to overwrite restore destination")
        restore_snapshot_to.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nomad-archive-inspect-",
                                     dir=restore_snapshot_to.parent if restore_snapshot_to is not None else None) as temp:
        db = Path(temp) / "archive.sqlite"
        _decompress(path, db, max_bytes)
        integrity_check(db)
        conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
        try:
            conn.enable_load_extension(False)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"header", "entries", "shards"}:
                raise ArchiveError("unrecognized archive database schema")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ArchiveError("orphan archive shards")
            header = dict(conn.execute("SELECT key,value FROM header"))
            if set(header) != {"manifest", "manifest_sha256"}:
                raise ArchiveError("unexpected manifest metadata")
            raw = header["manifest"].encode("ascii")
            if digest(raw) != header["manifest_sha256"]:
                raise ArchiveError("archive manifest digest mismatch")
            manifest = json.loads(raw)
            if canonical(manifest) != raw or manifest.get("version") != VERSION:
                raise ArchiveError("archive manifest invalid")
            entries = manifest.get("entries")
            if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
                raise ArchiveError("archive record count invalid")
            meta = conn.execute("SELECT entry_id,kind,reference,revision,byte_size,sha256,shard_count FROM entries ORDER BY entry_id").fetchall()
            if len(meta) != len(entries):
                raise ArchiveError("entry index/manifest mismatch")
            if not entries or not isinstance(entries[0], dict) or entries[0].get("kind") != "database":
                raise ArchiveError("missing first database entry")
            if conn.execute("SELECT COUNT(*) FROM shards").fetchone()[0] != sum(row[-1] for row in meta):
                raise ArchiveError("unexpected extra archived shard")
            tmp_restore = None
            if restore_snapshot_to is not None:
                tmp_restore = Path(temp) / "restored-source.sqlite"
            snapshots = 0
            for entry, (eid, kind, reference, revision, size, sha, shard_count) in zip(entries, meta):
                if not isinstance(entry, dict) or not isinstance(entry.get("shards"), list):
                    raise ArchiveError("malformed manifest entry")
                expected = {"kind": kind, "reference": reference, "revision": revision,
                            "byte_size": size, "sha256": sha,
                            "shards": entry.get("shards")}
                if entry != expected or size <= 0 or shard_count != len(entry.get("shards", [])):
                    raise ArchiveError("entry manifest mismatch")
                if kind == "database":
                    snapshots += 1
                    if (reference, revision) != ("sqlite-authority", "snapshot"):
                        raise ArchiveError("invalid snapshot identifier")
                h, count, seen = hashlib.sha256(), 0, 0
                out = None
                try:
                    if kind == "database" and tmp_restore is not None:
                        out = tmp_restore.open("xb")
                    for ordinal, block_sha, data in conn.execute("SELECT ordinal,sha256,payload FROM shards WHERE entry_id=? ORDER BY ordinal", (eid,)):
                        if ordinal != seen or len(data) > CHUNK or not data or block_sha != digest(data) or block_sha != entry["shards"][seen]:
                            raise ArchiveError("shard digest or order mismatch")
                        seen += 1
                        count += len(data)
                        h.update(data)
                        if out is not None:
                            out.write(data)
                    if count != size or h.hexdigest() != sha or seen != shard_count:
                        raise ArchiveError("archived record byte digest mismatch")
                    if out is not None:
                        out.flush()
                        os.fsync(out.fileno())
                finally:
                    if out is not None:
                        out.close()
            if snapshots != 1:
                raise ArchiveError("archive must contain one authority snapshot")
            if tmp_restore is not None:
                try:
                    integrity_check(tmp_restore)
                    os.link(tmp_restore, restore_snapshot_to)
                    fsync_dir(restore_snapshot_to.parent)
                finally:
                    tmp_restore.unlink(missing_ok=True)
            return {"archive_sha256": file_digest(path), "manifest_sha256": digest(raw),
                    "snapshot_sha256": entries[0]["sha256"], "record_count": len(entries)-1,
                    "manifest": manifest}
        except (sqlite3.Error, KeyError, IndexError, AttributeError, TypeError, ValueError) as e:
            if isinstance(e, ArchiveError):
                raise
            raise ArchiveError("invalid archive contents") from e
        finally:
            conn.close()


def restore_evidence(path: Path, destination_dir: Path, *, kind: str,
                     reference: str, revision: str) -> dict[str, Any]:
    """Recover one opaque historical record into an offline .bin with no overwrite.

    Original provider source paths are never used as local paths, and recovered
    files are intentionally not interpreted as code or executable artifacts.
    """
    verified = inspect_archive(path)  # verifies every archived byte first
    candidates = [entry for entry in verified["manifest"]["entries"]
                  if entry["kind"] == kind and entry["reference"] == reference
                  and entry["revision"] == revision]
    if len(candidates) != 1 or kind == "database":
        raise ArchiveError("historical evidence reference not found")
    entry = candidates[0]
    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = destination_dir / (entry["sha256"] + ".bin")
    if target.exists():
        raise ArchiveError("refusing to overwrite historical restore")
    with tempfile.TemporaryDirectory(prefix="nomad-evidence-restore-", dir=destination_dir) as td:
        archive_db = Path(td) / "archive.sqlite"
        _decompress(path, archive_db, MAX_ARCHIVE_BYTES)
        conn = sqlite3.connect(f"file:{archive_db.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
        try:
            row = conn.execute("SELECT entry_id FROM entries WHERE kind=? AND reference=? AND revision=?",
                               (kind, reference, revision)).fetchone()
            if row is None:
                raise ArchiveError("historical record absent from archive index")
            temp_out = Path(td) / "restored.bin"
            h = hashlib.sha256()
            size = 0
            with temp_out.open("xb") as out:
                for (block,) in conn.execute("SELECT payload FROM shards WHERE entry_id=? ORDER BY ordinal", (row[0],)):
                    h.update(block)
                    size += len(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if h.hexdigest() != entry["sha256"] or size != entry["byte_size"]:
                raise ArchiveError("historical record changed during restore")
            os.link(temp_out, target)
            fsync_dir(destination_dir)
        finally:
            conn.close()
    return {"path": str(target), "sha256": entry["sha256"], "byte_size": entry["byte_size"],
            "kind": kind, "reference": reference, "revision": revision}
