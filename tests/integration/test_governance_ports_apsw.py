"""Verified Bible-byte reads check project, purpose, size and physical digest."""
import hashlib
import pytest
apsw=pytest.importorskip("apsw")
from studio.governance.model import GovernanceError
from studio.storage.governance_ports import VerifiedGovernanceBlobSource, CanonImpactAPSWCatalog
from studio.storage.workforce import WorkforceStorage


def test_bible_bytes_are_project_and_purpose_bound(tmp_path):
    db=WorkforceStorage.open(tmp_path/"project.sqlite",fresh=True)
    try:
        c=db._connection
        for p in ("p1","p2"):
            c.execute("INSERT INTO projects(project_id,name,charter_digest,state) "
                      "VALUES(?,?,?,'active')",(p,p,"digest"))
        source=VerifiedGovernanceBlobSource(db,tmp_path/"bytes")
        stored=source.content.put(b"canonical original Bible content")
        c.execute("INSERT INTO blobs VALUES (?,?,?,'verified')",
                  (stored.digest,stored.byte_size,stored.store_key))
        c.execute("INSERT INTO project_blobs VALUES(?,?,?)",
                  ("p1",stored.digest,"bible-content"))
        assert source.read_verified("p1",stored.digest,"bible-content")==b"canonical original Bible content"
        with pytest.raises(GovernanceError,match="project"):
            source.read_verified("p2",stored.digest,"bible-content")
        with pytest.raises(GovernanceError,match="project"):
            source.read_verified("p1",stored.digest,"bible-manifest")
        c.execute("UPDATE blobs SET store_key='other' WHERE content_digest=?",(stored.digest,))
        with pytest.raises(GovernanceError,match="different bytes"):
            source.read_verified("p1",stored.digest,"bible-content")
        assert CanonImpactAPSWCatalog(db).referenced_tasks("p1",set())==set()
    finally:
        db.close()
