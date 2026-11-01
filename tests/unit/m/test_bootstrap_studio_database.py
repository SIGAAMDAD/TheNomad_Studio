"""Deployment database initialization is explicit and never destructive."""
from pathlib import Path

import pytest

apsw = pytest.importorskip('apsw')
from scripts.bootstrap_studio_database import bootstrap_database
from studio.storage.workforce import WorkforceStorage


def test_bootstrap_then_reopen_existing_database(tmp_path):
    path = tmp_path / 'persist' / 'studio.sqlite3'
    bootstrap_database(path)
    assert path.is_file()
    store = WorkforceStorage.open(path, fresh=False)
    store.close()
    with pytest.raises(ValueError, match='existing state'):
        bootstrap_database(path)


def test_bootstrap_refuses_memory_pseudo_path(tmp_path):
    with pytest.raises(ValueError, match='on-disk'):
        bootstrap_database(Path(':memory:'))
