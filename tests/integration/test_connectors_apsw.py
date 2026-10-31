"""G connector bindings survive C APSW reopening and reject cross-replay."""
import pytest
apsw=pytest.importorskip("apsw")

from studio.integrations.common import IntegrationError
from studio.storage.connectors import ConnectorAPSWRepository
from studio.storage.workforce import WorkforceStorage


def test_connector_replay_and_project_isolation(tmp_path):
    path=tmp_path/"connector.sqlite"
    writer=WorkforceStorage.open(path,fresh=True)
    try:
        conn=writer._connection
        for project in ("p1","p2"):
            conn.execute("INSERT INTO projects(project_id,name,charter_digest,state) "
                         "VALUES(?,?,?,'active')",(project,project,"digest"))
        repo=ConnectorAPSWRepository(writer)
        repo.record_connector("p1","github","credential-1","account","attach-1")
        repo.record_connector("p1","github","credential-1","account","attach-1")
        assert writer.read("p1",lambda tx:tx.one(
            "SELECT provider_key,credential_ref,enabled FROM external_accounts "
            "WHERE project_id=? AND account_id=?",("p1","account")))=={
                "provider_key":"github","credential_ref":"credential-1","enabled":1}
        assert writer.read("p2",lambda tx:tx.one(
            "SELECT account_id FROM external_accounts WHERE project_id=?",
            ("p2",))) is None
        with pytest.raises(IntegrationError,match="different content"):
            repo.record_connector("p1","github","credential-2","account","attach-1")
        with pytest.raises(IntegrationError,match="already bound"):
            repo.record_connector("p1","google_drive","credential-2","account","attach-2")
        repo.record_connector("p2","github","credential-1","account","attach-1")
        assert writer.read("p2",lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM external_accounts WHERE project_id=?",
            ("p2",)))["n"]==1
    finally:
        writer.close()
    reopened=WorkforceStorage.open(path,fresh=False)
    try:
        repo=ConnectorAPSWRepository(reopened)
        repo.record_connector("p1","github","credential-1","account","attach-1")
    finally:
        reopened.close()
