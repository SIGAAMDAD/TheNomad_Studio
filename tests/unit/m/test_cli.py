import pytest
from studio.app.cli import load_composition, main
from studio.app.errors import StudioError


def test_missing_composition_factory_fail_closed(monkeypatch):
    monkeypatch.delenv('STUDIO_COMPOSITION_FACTORY',raising=False)
    assert main()==78
    with pytest.raises(StudioError) as exc:
        load_composition('')
    assert exc.value.code=='capability_unavailable'


def test_invalid_composition_factory_has_no_fallback():
    with pytest.raises(StudioError):
        load_composition('not_a_module_or_factory')
