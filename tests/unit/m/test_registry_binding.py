from types import SimpleNamespace
from unittest.mock import Mock

from studio.app.composition import StudioComposition
from studio.api.service import DomainRegistry


def _service(*names):
    return SimpleNamespace(**{name: Mock(return_value={"ok": True}) for name in names})


def test_studio_exposes_every_frozen_operation_as_registry_callable():
    m = StudioComposition(
        projects=_service('createProject','listProjects','getProject','configureProject','preflightProject'),
        releases=_service('prepareRelease','publishRelease','listReleases'),
        recovery=_service('requestRestore','observeRecovery'),
        runtime=object(),
        integrations=_service('stageIntegration','promoteIntegration','inspectIntegration'),
    )
    registry = DomainRegistry()
    registry.register_owner('M', m)
    for name in m.operation_ids:
        handler = registry.get(SimpleNamespace(owner='M', name=name))
        assert callable(handler), name
    assert registry.missing(SimpleNamespace(operations={name: SimpleNamespace(owner='M', name=name) for name in m.operation_ids})) == []
    assert registry.get(SimpleNamespace(owner='M', name='listProjects'))({'principal_id':'test'}) == {'ok':True}


def test_studio_refuses_undefined_operation_and_unexpected_parameters():
    m = StudioComposition(projects=_service('listProjects'), releases=object(), recovery=object(), runtime=object())
    assert not hasattr(m, 'unapprovedAction')
    h = m.listProjects
    from studio.app.errors import StudioError
    try:
        h({}, invented='parameter')
    except StudioError:
        pass
    else:
        raise AssertionError('unfrozen path parameter was accepted')
