"""A removed JSON controller cannot silently bypass R5R source-target policy."""
from pathlib import Path
import importlib.util

import pytest
from studio.app.source_policy import (SOURCE_ROUTES, SourceTarget,
                                      validate_source_job, validate_source_target)
from studio.app.errors import StudioError


def bound(workstream):
    repo, ref = SOURCE_ROUTES[workstream]
    return SourceTarget('project-a', workstream, repo, ref, repo)


@pytest.mark.parametrize('workstream', sorted(SOURCE_ROUTES))
def test_owner_source_targets_pass_with_pinned_sha(workstream):
    target = bound(workstream)
    validate_source_target(target, project_id='project-a', repo_id=target.repo_id,
                           commit_sha='a'*40)
    validate_source_job(target=target, project_id='project-a',
                        workstream=workstream, repo_id=target.repo_id,
                        commit_sha='a'*40, language='C#' if workstream == 'mod_gameplay' else None)


@pytest.mark.parametrize('mutation', ['project', 'repository', 'ref', 'workstream', 'repo_id', 'sha'])
def test_invalid_or_cross_project_source_target_rejected(mutation):
    target = bound('rendering_engine')
    kwargs = {'project_id':'project-a','repo_id':target.repo_id,'commit_sha':'a'*40}
    if mutation == 'project': kwargs['project_id'] = 'project-b'
    if mutation == 'repo_id': kwargs['repo_id'] = 'SIGAAMDAD/NomadFramework'
    if mutation == 'sha': kwargs['commit_sha'] = 'unverified'
    if mutation in {'repository','ref','workstream'}:
        from dataclasses import replace
        target = replace(target, **{mutation: {'repository':'SIGAAMDAD/evil', 'ref':'wrong', 'workstream':'goap_core'}[mutation]})
    with pytest.raises(StudioError):
        validate_source_target(target, **kwargs)


def test_mod_code_requires_csharp():
    target = bound('mod_gameplay')
    with pytest.raises(StudioError):
        validate_source_job(target=target, project_id=target.project_id,
                            workstream=target.workstream, repo_id=target.repo_id,
                            commit_sha='c'*40, language='Python')


def test_unauthorized_workstream_is_rejected():
    target = bound('goap_core')
    with pytest.raises(StudioError):
        validate_source_job(target=target, project_id=target.project_id,
                            workstream='core_gameplay', repo_id=target.repo_id,
                            commit_sha='c'*40)
