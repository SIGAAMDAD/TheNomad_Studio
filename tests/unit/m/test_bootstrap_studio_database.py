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


def test_bootstrap_refuses_existing_empty_file(tmp_path):
    path = tmp_path / 'studio.sqlite3'
    path.touch()
    with pytest.raises(ValueError, match='existing state'):
        bootstrap_database(path)
    assert path.stat().st_size == 0


def test_bootstrap_refuses_symlink(tmp_path):
    occupied = tmp_path / 'original.sqlite3'
    occupied.write_bytes(b'preserve')
    alias = tmp_path / 'alias.sqlite3'
    alias.symlink_to(occupied)
    with pytest.raises(ValueError, match='existing state'):
        bootstrap_database(alias)
    assert occupied.read_bytes() == b'preserve'
