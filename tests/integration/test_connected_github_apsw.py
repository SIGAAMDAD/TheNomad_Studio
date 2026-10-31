"""G provider composition binds live GitHub identity to durable project authority."""
import json

import pytest
apsw=pytest.importorskip("apsw")

from studio.app.github_providers import (
    ConnectedGitHubProject, ConnectedGitHubProviderBuilder,
)
from studio.integrations.ci import BuildTarget
from studio.integrations.common import IntegrationError
from studio.storage.workforce import WorkforceStorage


class GitHubAccountHTTP:
    def __init__(self, login="alice"):
        self.login=login
        self.requests=[]

    def request(self, method, url, headers, body=None):
        self.requests.append((method,url,headers.get("Authorization","")))
        assert method=="GET"
        assert url=="https://api.github.com/user"
        return 200,{},json.dumps({"login":self.login}).encode()


def test_real_project_provider_uses_c_writer_and_git_account_verification(tmp_path):
    writer=WorkforceStorage.open(tmp_path/"studio.sqlite",fresh=True)
    try:
        c=writer._connection
        for project in ("p1","p2"):
            c.execute("INSERT INTO projects VALUES(?,?,?,1,'active')",
                      (project,project,"digest"))
        c.execute("INSERT INTO project_repositories(project_id,repo_id,provider_key,"
                  "remote_ref) VALUES('p1','repository','github','main')")
        c.execute("INSERT INTO relay_channels VALUES(?,?,?,?,?,1)",
                  ("p1","comms","repository","studio/comms",".nomad/relays/p1"))
        c.execute("INSERT INTO external_accounts(project_id,account_id,provider_key,"
                  "credential_ref,enabled) VALUES('p1','alice','github','git-credential',1)")
        http=GitHubAccountHTTP()
        configuration=ConnectedGitHubProject(
            project_id="p1",repo_id="repository",account_id="alice",
            owner="alice",repository="Nomad",source_ref="main",comms_ref="studio/comms",
            builds=(BuildTarget("compile","build.yml","main"),),
            effect_path=lambda typ,subject,action:f".nomad/effects/{subject}.json")
        builder=ConnectedGitHubProviderBuilder((configuration,),
            credential_resolver=lambda key:"real-token" if key=="git-credential" else "",
            http=http)
        bound=builder.for_writer(writer,tmp_path/"bytes")
        service=bound("p1")
        assert service.binding.project_id=="p1"
        assert service.actions.github.channel.ref=="studio/comms"
        assert service.actions.targets["compile"].workflow_id=="build.yml"
        assert service.list_artifacts(project_id="p1")=={"artifacts":[]}
        assert len(http.requests)==1
        with pytest.raises(IntegrationError,match="not installed"):
            bound("p2")
        http.login="other"
        with pytest.raises(IntegrationError,match="identity changed"):
            bound("p1")
        http.login="alice"
        c.execute("UPDATE external_accounts SET enabled=0 WHERE project_id='p1'")
        with pytest.raises(IntegrationError,match="binding invalid"):
            bound("p1")
        c.execute("UPDATE external_accounts SET enabled=1 WHERE project_id='p1'")
        c.execute("UPDATE relay_channels SET enabled=0 WHERE project_id='p1'")
        with pytest.raises(IntegrationError,match="binding invalid"):
            bound("p1")
    finally:
        writer.close()


def test_wrong_build_target_or_project_provider_refused(tmp_path):
    with pytest.raises(IntegrationError,match="duplicate"):
        ConnectedGitHubProject("p","repo","alice","alice","repo",
                               "main","comms",(BuildTarget("same","a.yml","main"),
                                              BuildTarget("same","b.yml","main")),
                               lambda *_:"path")
    with pytest.raises(TypeError,match="credential"):
        ConnectedGitHubProviderBuilder((),credential_resolver=None)
