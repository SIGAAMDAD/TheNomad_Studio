"""Authenticated per-project GitHub providers backed by Studio's C-owned APSW.

Every GitHub channel is an existing configured repository/ref. Credential tokens
are obtained from a trusted server-side resolver; neither tokens nor arbitrary
GitHub targets may enter through an operator request.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from studio.integrations.api_ports import GOperatorPorts
from studio.integrations.ci import BuildTarget, GitHubActions
from studio.integrations.common import IntegrationError, ProjectBinding, identifier
from studio.integrations.connectors import ConnectorService
from studio.integrations.effects import EffectReconciler
from studio.integrations.github import GitHub, GitHubChannel
from studio.integrations.http import HttpPort, UrllibHttp, JsonProvider
from studio.storage.artifact_catalog import ArtifactAPSWCatalog
from studio.storage.builds import BuildAPSWRepository
from studio.storage.connectors import ConnectorAPSWRepository
from studio.storage.provider_effects import EffectAPSWRepository
from studio.storage.workforce import WorkforceStorage


@dataclass(frozen=True)
class ConnectedGitHubProject:
    project_id: str
    repo_id: str
    account_id: str
    owner: str
    repository: str
    source_ref: str
    comms_ref: str
    builds: tuple[BuildTarget, ...]
    effect_path: Callable[[str,str,str],str]

    def __post_init__(self):
        identifier(self.project_id)
        identifier(self.repo_id)
        identifier(self.account_id)
        if not callable(self.effect_path):
            raise IntegrationError('invalid','trusted exact effect path resolver required')
        GitHubChannel(self.project_id,self.owner,self.repository,self.comms_ref)
        GitHubChannel(self.project_id,self.owner,self.repository,self.source_ref)
        target_names=[entry.target_id for entry in self.builds]
        if len(target_names)!=len(set(target_names)):
            raise IntegrationError("invalid","duplicate approved CI workflow target")


class ConnectedGitHubProviders:
    """A real GOperatorPorts factory consuming exact preapproved deploy config."""

    def __init__(
        self, *, writer: WorkforceStorage, artifact_root: str | Path,
        projects: tuple[ConnectedGitHubProject, ...],
        credential_resolver: Callable[[str], str],
        http: HttpPort | None = None,
    ):
        if not isinstance(writer,WorkforceStorage) or not callable(credential_resolver):
            raise TypeError("real APSW and server-side credential resolver required")
        if len({p.project_id for p in projects})!=len(projects):
            raise IntegrationError("invalid","duplicate GitHub project configuration")
        self.writer=writer
        self.artifact_root=Path(artifact_root)
        self.projects={p.project_id:p for p in projects}
        self.credential_resolver=credential_resolver
        self.http=http if http is not None else UrllibHttp()

    def __call__(self, project_id: str) -> GOperatorPorts:
        identifier(project_id)
        target=self.projects.get(project_id)
        if target is None:
            raise IntegrationError("capability_unavailable","project GitHub provider not installed")
        with self.writer._lock:
            repo=self.writer._connection.execute(
                "SELECT provider_key,remote_ref FROM project_repositories "
                "WHERE project_id=? AND repo_id=?", (project_id,target.repo_id)).fetchone()
            account=self.writer._connection.execute(
                "SELECT provider_key,credential_ref,enabled FROM external_accounts "
                "WHERE project_id=? AND account_id=?", (project_id,target.account_id)).fetchone()
            channel=self.writer._connection.execute(
                "SELECT 1 FROM relay_channels WHERE project_id=? AND repo_id=? "
                "AND git_ref=? AND enabled=1",
                (project_id,target.repo_id,target.comms_ref)).fetchone()
        if (repo is None or repo[0]!="github" or repo[1]!=target.source_ref
                or channel is None or account is None
                or account[0]!="github" or account[2]!=1):
            raise IntegrationError("forbidden","GitHub project/repository/channel/account binding invalid")
        token=self.credential_resolver(account[1])
        if not isinstance(token,str) or not token:
            raise IntegrationError("capability_unavailable","configured GitHub credential unavailable")
        verified=JsonProvider(self.http,lambda:token,"https://api.github.com").request("GET","/user")
        if not isinstance(verified,dict) or verified.get("login")!=target.account_id:
            raise IntegrationError("forbidden","configured GitHub credential identity changed")
        binding=ProjectBinding(project_id,target.account_id)
        github=GitHub(self.http,lambda:token,
                      GitHubChannel(project_id,target.owner,target.repository,target.comms_ref),
                      binding)
        build_targets={entry.target_id:entry for entry in target.builds}
        return GOperatorPorts(
            binding=binding,
            artifacts=ArtifactAPSWCatalog(self.writer,self.artifact_root),
            connectors=ConnectorService(
                self.http,binding,self.credential_resolver,
                ConnectorAPSWRepository(self.writer)),
            actions=GitHubActions(github,binding,build_targets),
            builds=BuildAPSWRepository(self.writer),
            effects=EffectReconciler(
                binding, EffectAPSWRepository(
                    self.writer,
                    lambda p,provider,typ,subject,action: (
                        f"{target.owner}/{target.repository}@{target.comms_ref}",
                        target.effect_path(typ,subject,action)))),
        )


class ConnectedGitHubProviderBuilder:
    """Configure G once, bind each project to the *same* C writer at startup."""

    def __init__(self, projects: tuple[ConnectedGitHubProject, ...],
                 credential_resolver: Callable[[str], str],
                 http: HttpPort | None = None):
        if not callable(credential_resolver):
            raise TypeError("trusted credential resolver required")
        self.projects=projects
        self.credential_resolver=credential_resolver
        self.http=http

    def for_writer(self, writer: WorkforceStorage,
                   artifact_root: str | Path) -> ConnectedGitHubProviders:
        return ConnectedGitHubProviders(
            writer=writer,artifact_root=artifact_root,projects=self.projects,
            credential_resolver=self.credential_resolver,http=self.http)
