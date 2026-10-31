"""The Nomad owner-selected source targets enforced at the trusted R5R edge.

No separate runtime scheduler or JSON source registry exists. Source targets are
fixed owner selections. An authenticated per-project binding and exact remote
Git readback are still required to write or promote source.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

from .errors import StudioError

# Owner-authored workstream selection; changing these routes requires owner review.
SOURCE_ROUTES = {
    'rendering_engine': ('SIGAAMDAD/Terrain3D', 'Terrain3D_NomadRenderer'),
    'framework_engine': ('SIGAAMDAD/NomadFramework', 'develop'),
    'core_gameplay': ('SIGAAMDAD/The_Nomad', 'Nomad3D'),
    'mod_gameplay': ('SIGAAMDAD/NomadMain', 'main'),
    'goap_core': ('SIGAAMDAD/TheNomad_GOAP', 'main'),
}
_SHA = re.compile(r'[0-9a-f]{40}\Z')

@dataclass(frozen=True)
class SourceTarget:
    project_id: str
    workstream: str
    repository: str
    ref: str
    repo_id: str


def validate_source_target(target: SourceTarget, *, project_id: str,
                           repo_id: str, commit_sha: str) -> None:
    """Check the trusted project binding against owner routes and real SHA."""
    if not isinstance(target, SourceTarget) or target.project_id != project_id:
        raise StudioError('forbidden', 'trusted project source target is required')
    if not isinstance(repo_id, str) or not repo_id or repo_id != target.repo_id:
        raise StudioError('forbidden', 'source revision is from a different repository')
    if SOURCE_ROUTES.get(target.workstream) != (target.repository, target.ref):
        raise StudioError('forbidden', 'source route differs from owner-selected target')
    if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha):
        raise StudioError('invalid', 'pinned remote Git commit SHA required')


def validate_source_job(*, target: SourceTarget, project_id: str,
                        workstream: str, repo_id: str, commit_sha: str,
                        language: str | None = None) -> None:
    validate_source_target(target, project_id=project_id, repo_id=repo_id,
                           commit_sha=commit_sha)
    if workstream != target.workstream:
        raise StudioError('forbidden', 'workstream is not authorized for this source binding')
    if workstream == 'mod_gameplay' and language != 'C#':
        raise StudioError('forbidden', 'mod-specific gameplay source requires C#')
