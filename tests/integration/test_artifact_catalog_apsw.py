"""G original artifact bytes and C durable SQL index must agree and survive restart."""
import pytest
apsw=pytest.importorskip("apsw")
from studio.integrations.artifacts import Artifact
from studio.integrations.common import IntegrationError
from studio.storage.artifact_catalog import ArtifactAPSWCatalog
from studio.storage.workforce import WorkforceStorage
from studio.storage.governance_ports import VerifiedGovernanceBlobSource


def test_artifacts_original_bytes_and_cross_project_fence(tmp_path):
    path=tmp_path/"studio.sqlite"
    root=tmp_path/"blob-store"
    db=WorkforceStorage.open(path,fresh=True)
    try:
        c=db._connection
        for project in ("p1","p2"):
            c.execute("INSERT INTO projects VALUES(?,?,?,1,'active')",
                      (project,project,"digest"))
        catalog=ArtifactAPSWCatalog(db,root)
        body=b"real original source bytes"
        original=catalog.put_verified(project_id="p1",content=body,purpose="artifact-content")
        manifest=catalog.put_verified(project_id="p1",
                                      content=b'{"kind":"source"}',purpose="artifact-manifest")
        entry=Artifact("p1","a1","source",original,manifest,
                       None,None,None,None,"sha256:"+original)
        catalog.register(entry)
        catalog.register(entry)
        assert catalog.get(project_id="p1",digest=original)==body
        assert catalog.get_artifact(project_id="p1",artifact_id="a1")==entry
        assert catalog.list_artifacts(project_id="p1")==[entry.summary()]
        assert catalog.list_artifacts(project_id="p2")==[]
        with pytest.raises(IntegrationError,match="not shared"):
            catalog.get(project_id="p2",digest=original)
        with pytest.raises(IntegrationError,match="unavailable"):
            catalog.get_artifact(project_id="p2",artifact_id="a1")
        second=catalog.put_verified(project_id="p1",content=b"different",purpose="artifact-content")
        with pytest.raises(IntegrationError,match="different content"):
            catalog.register(Artifact("p1","a1","source",second,manifest,
                None,None,None,None,"sha256:"+second))
        with pytest.raises(Exception,match="verified project Bible blob"):
            VerifiedGovernanceBlobSource(db,root).read_verified(
                "p1",original,"bible-content")
        catalog.put_verified(project_id="p1",content=body,purpose="bible-content")
        assert VerifiedGovernanceBlobSource(db,root).read_verified(
            "p1",original,"bible-content")==body
        assert c.execute("SELECT COUNT(*) FROM artifacts WHERE project_id='p1'").fetchone()[0]==1
    finally:
        db.close()
    reopened=WorkforceStorage.open(path,fresh=False)
    try:
        catalog=ArtifactAPSWCatalog(reopened,root)
        assert catalog.get_artifact(project_id="p1",artifact_id="a1")==entry
        assert catalog.get(project_id="p1",digest=original)==body
    finally:
        reopened.close()


def test_corrupt_physical_artifact_detected(tmp_path):
    db=WorkforceStorage.open(tmp_path/"corrupt.sqlite",fresh=True)
    try:
        db._connection.execute("INSERT INTO projects VALUES('p','p','digest',1,'active')")
        repo=ArtifactAPSWCatalog(db,tmp_path/"bytes")
        digest=repo.put_verified(project_id="p",content=b"intact",purpose="artifact-content")
        repo.bytes._file(digest).write_bytes(b"corrupt")
        with pytest.raises(IntegrationError,match="absent or corrupt"):
            repo.get(project_id="p",digest=digest)
    finally:
        db.close()
