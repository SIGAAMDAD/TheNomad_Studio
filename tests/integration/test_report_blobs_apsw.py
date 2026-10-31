"""F profile and output report bytes are physically verified and project fenced."""
import hashlib

import pytest
apsw=pytest.importorskip("apsw")

from studio.operations.core import DomainError
from studio.storage.report_blobs import VerifiedReportBlobsBuilder
from studio.storage.artifact_catalog import ArtifactAPSWCatalog
from studio.storage.workforce import WorkforceStorage


def test_profile_input_and_report_output_are_original_and_durable(tmp_path):
    path=tmp_path/"report.sqlite"
    root=tmp_path/"bytes"
    db=WorkforceStorage.open(path,fresh=True)
    try:
        c=db._connection
        for project in ("p1","p2"):
            c.execute("INSERT INTO projects VALUES(?,?,?,1,'active')",
                      (project,project,"digest"))
        artifact=ArtifactAPSWCatalog(db,root)
        profile=b'{"report":"operating"}'
        digest=artifact.put_verified(project_id="p1",content=profile,purpose="report-profile")
        adapter=VerifiedReportBlobsBuilder().for_writer(db,root)
        assert adapter.load_verified("p1",digest)==profile
        with pytest.raises(DomainError,match="profile was not project-verified"):
            adapter.load_verified("p2",digest)
        report=b'{"kind":"monthly","totals":[]}'
        output=adapter.store_verified("p1",report,purpose="report")
        assert output==hashlib.sha256(report).hexdigest()
        assert artifact.get(project_id="p1",digest=output)==report
        with pytest.raises(DomainError,match="only verified report"):
            adapter.store_verified("p1",b"unapproved",purpose="bible-manifest")
        assert db.read("p1",lambda tx:tx.one(
            "SELECT 1 AS ok FROM project_blobs WHERE project_id=? "
            "AND content_digest=? AND purpose='report'",
            ("p1",output)))=={"ok":1}
    finally:
        db.close()
    reopened=WorkforceStorage.open(path,fresh=False)
    try:
        assert VerifiedReportBlobsBuilder().for_writer(reopened,root).load_verified(
            "p1",digest)==profile
    finally:
        reopened.close()
