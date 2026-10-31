"""Production GitHub command publisher using C's actual project/account bindings.

A deployer installs this builder through TrustedPorts.command_delivery. No
worker browser is started, and no token is supplied by operator API requests.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from studio.integrations.github import GitHub
from studio.integrations.http import HttpPort
from studio.storage.workforce import WorkforceStorage
from studio.workforce import Workforce

from .command_delivery import CommandDeliveryPump
from .github_providers import ConnectedGitHubProject, ConnectedGitHubProviders


class ConnectedGitHubCommandDeliveryBuilder:
    """Resolve G providers against C for every project, then publish from I."""

    def __init__(self, *, projects: tuple[ConnectedGitHubProject, ...],
                 credential_resolver: Callable[[str], str],
                 actor_for_project: Callable[[str], Mapping],
                 http: HttpPort | None = None,
                 interval_seconds: float = 3.0,
                 projects_per_poll: int = 8,
                 commands_per_project: int = 1):
        if not callable(actor_for_project):
            raise TypeError("trusted publication actor resolver required")
        self.projects = projects
        self.credential_resolver = credential_resolver
        self.actor_for_project = actor_for_project
        self.http = http
        self.interval_seconds = interval_seconds
        self.projects_per_poll = projects_per_poll
        self.commands_per_project = commands_per_project

    def for_writer(self, writer: WorkforceStorage, artifact_root: str | Path,
                   workforce: Workforce) -> CommandDeliveryPump:
        providers = ConnectedGitHubProviders(
            writer=writer, artifact_root=artifact_root,
            projects=self.projects,
            credential_resolver=self.credential_resolver,
            http=self.http)

        def verified_github(project_id: str) -> GitHub:
            ports = providers(project_id)
            github = ports.relay
            if not isinstance(github, GitHub) or github.binding.project_id != project_id:
                raise TypeError("G returned an unbound GitHub relay transport")
            return github

        return CommandDeliveryPump(
            writer=writer, workforce=workforce,
            actor_for_project=self.actor_for_project,
            github_for_project=verified_github,
            interval_seconds=self.interval_seconds,
            projects_per_poll=self.projects_per_poll,
            commands_per_project=self.commands_per_project)
