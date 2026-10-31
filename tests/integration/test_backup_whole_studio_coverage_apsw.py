"""Recovery tests exercise actual SQLite backup, verified content and fresh restoration.

These are production-driver tests, not in-memory mocks.  They must run in the
same CI job that instruments the entire src/studio package.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

apsw = pytest.importorskip("apsw")

from studio.storage.backup import BackupManager, BackupDescriptor
from studio.storage.blobs import ContentAddressedStore
from studio.storage.engine import ScopedStore
from studio.storage.errors import Conflict, Invalid, Missing


@pytest.fixture
def whole_studio(tmp_path):
    store = ScopedStore(tmp_path / "main.sqlite", fresh=True)
    blobs = ContentAddressedStore(tmp_path / "blobs")
    manager = BackupManager(blobs)
    try:
        yield store, blobs, manager, tmp_path
    finally:
        store.close()


def test_whole_studio_backup_verifies_restores_to_fresh_db(whole_studio):
    store, blobs, manager, tmp = whole_studio
    descriptor = manager.create_whole_studio(store)
    assert descriptor.blob_count == 0
    assert len(descriptor.backup_digest) == 64
    manifest = manager.verify(descriptor)
    assert manifest["format"] == "nomad-studio-r5-whole-studio-backup"
    assert manifest["blobs"] == []
    manager.verify_sqlite_image(descriptor)
    restored = tmp / "restored.sqlite"
    log = tmp / "recovery.log"
    receipt = manager.restore_to_fresh_database(descriptor, restored, recovery_log=log)
    assert len(receipt) == 64
    assert restored.is_file()
    conn = apsw.Connection(str(restored))
    try:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert not list(conn.execute("PRAGMA foreign_key_check"))
    finally:
        conn.close()
    records = [json.loads(line) for line in log.read_bytes().splitlines()]
    assert len(records) == 1
    assert records[0]["backup_digest"] == descriptor.backup_digest
    assert records[0]["restored_database_sha256"] == descriptor.database_blob_digest
    with pytest.raises(Conflict, match="new database"):
        manager.restore_to_fresh_database(descriptor, restored, recovery_log=log)
    assert len(log.read_bytes().splitlines()) == 1


def test_restore_never_overwrites_existing_path_or_creates_missing_directory(whole_studio):
    store, blobs, manager, tmp = whole_studio
    descriptor = manager.create_whole_studio(store)
    occupied = tmp / "occupied.sqlite"
    occupied.write_bytes(b"untouched")
    for target in (occupied, tmp / "missing_parent" / "recovered.sqlite"):
        with pytest.raises(Conflict):
            manager.restore_to_fresh_database(descriptor, target, recovery_log=tmp/"receipt.jsonl")
    assert occupied.read_bytes() == b"untouched"
    assert not (tmp / "receipt.jsonl").exists()


@pytest.mark.parametrize("change", [
    "bad-backup-digest", "wrong-db-digest", "wrong-manifest-digest", "wrong-schema-digest",
    "wrong-blob-count",
])
def test_tampered_backup_descriptor_never_accepted(whole_studio, change):
    store, blobs, manager, tmp = whole_studio
    descriptor = manager.create_whole_studio(store)
    replacements = {
        "bad-backup-digest": {"backup_digest": "f"*64},
        "wrong-db-digest": {"database_blob_digest": "0"*64},
        "wrong-manifest-digest": {"manifest_digest": "0"*64},
        "wrong-schema-digest": {"schema_digest": "0"*64},
        "wrong-blob-count": {"blob_count": 999},
    }
    damaged = replace(descriptor, **replacements[change])
    with pytest.raises((Invalid, Missing)):
        manager.verify(damaged)
    assert not (tmp/"restored.sqlite").exists()


def test_manifest_byte_corruption_rejects_backup_and_restore(whole_studio):
    store, blobs, manager, tmp = whole_studio
    descriptor = manager.create_whole_studio(store)
    path = blobs._file(descriptor.manifest_digest)
    original = path.read_bytes()
    path.write_bytes(b"{}")
    try:
        with pytest.raises(Invalid):
            manager.verify(descriptor)
        with pytest.raises(Invalid):
            manager.restore_to_fresh_database(descriptor,tmp/"output.sqlite",recovery_log=tmp/"receipt")
    finally:
        path.write_bytes(original)
    assert not (tmp/"output.sqlite").exists()


def test_backup_refuses_open_transaction(whole_studio):
    store, blobs, manager, tmp = whole_studio
    store._conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(Conflict, match="open write transaction"):
            manager.create_whole_studio(store)
    finally:
        store._conn.execute("ROLLBACK")
    assert manager.verify(manager.create_whole_studio(store))["blobs"] == []


def test_backup_integrity_detects_mismatched_manifest_fields(whole_studio):
    store, blobs, manager, tmp = whole_studio
    descriptor = manager.create_whole_studio(store)
    manifest = manager.verify(descriptor)
    for key, value in (("format", "unrecognized"),("schema_sha256", "0"*64),
                       ("database_size", -1),("blobs", "not a list")):
        modified = dict(manifest)
        modified[key] = value
        changed = blobs.put(json.dumps(modified,sort_keys=True,separators=(",", ":")).encode())
        next_descriptor = replace(descriptor, manifest_digest=changed.digest,
                                  backup_digest=hashlib.sha256((descriptor.database_blob_digest+":"+changed.digest).encode()).hexdigest())
        with pytest.raises(Invalid):
            manager.verify(next_descriptor)


def test_whole_studio_backup_includes_and_rechecks_registered_blob_bytes(whole_studio):
    store,blobs,manager,tmp=whole_studio
    stored=blobs.put(b'original durable source blob')
    store._conn.execute('INSERT INTO blobs(content_digest,byte_size,store_key,durability) '
                        "VALUES(?,?,?,'verified')",(stored.digest,stored.byte_size,stored.store_key))
    descriptor=manager.create_whole_studio(store)
    assert descriptor.blob_count==1
    assert manager.verify(descriptor)['blobs']==[{'digest':stored.digest,'size':stored.byte_size}]
    manager.verify_sqlite_image(descriptor)
    original=blobs._file(stored.digest).read_bytes()
    blobs._file(stored.digest).write_bytes(b'corruption')
    try:
        with pytest.raises(Invalid):manager.verify(descriptor)
        with pytest.raises(Invalid):manager.create_whole_studio(store)
    finally:
        blobs._file(stored.digest).write_bytes(original)
    assert manager.verify(descriptor)['blobs'][0]['digest']==stored.digest


def test_backup_inventory_size_mismatch_fails_without_publication(whole_studio):
    store,blobs,manager,tmp=whole_studio
    stored=blobs.put(b'actual bytes')
    store._conn.execute('INSERT INTO blobs(content_digest,byte_size,store_key,durability) '
                        "VALUES(?,?,?,'verified')",(stored.digest,stored.byte_size+1,stored.store_key))
    with pytest.raises(Invalid,match='size'):
        manager.create_whole_studio(store)


def test_backup_registration_requires_c_and_both_verified_references(whole_studio,monkeypatch):
    from studio.storage.errors import Forbidden
    store,blobs,manager,tmp=whole_studio
    descriptor=manager.create_whole_studio(store)
    monkeypatch.setattr(manager,'verify_sqlite_image',lambda _:None)
    class Cursor:
        def __init__(self,ok):self.ok=ok
        def fetchone(self):return (1,) if self.ok else None
    class Tx:
        def __init__(self,owner,ok):self.domain_owner=owner;self.ok=ok;self.calls=[];self.project_id='p'
        def _execute(self,sql,params):self.calls.append((sql,params));return Cursor(self.ok)
    with pytest.raises(Forbidden):manager.register_verified(Tx('WG-I',True),descriptor,created_at='now')
    with pytest.raises(Missing):manager.register_verified(Tx('WG-C',False),descriptor,created_at='now')
    authorized=Tx('WG-C',True)
    manager.register_verified(authorized,descriptor,created_at='now')
    assert any('INSERT INTO backup_sets' in sql for sql,args in authorized.calls)