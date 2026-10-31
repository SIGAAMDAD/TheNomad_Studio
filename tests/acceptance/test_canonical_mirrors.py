"""Frozen canonical bytes and distributed skill mirrors are mechanically checked."""
import shutil
from pathlib import Path
import pytest
from scripts.verify_canonical_mirrors import verify, verify_pair, verify_catalog

ROOT = Path(__file__).resolve().parents[2]


def test_full_repository_role_and_contract_mirrors():
    verify(ROOT)


def test_modified_mirror_is_rejected(tmp_path):
    (tmp_path/'CONTRACTS').mkdir()
    (tmp_path/'schema').mkdir()
    (tmp_path/'CONTRACTS/initial_schema.sql').write_bytes(b'canonical')
    mirror=tmp_path/'schema/initial_schema.sql'
    mirror.write_bytes(b'canonical')
    verify_pair(tmp_path,'CONTRACTS/initial_schema.sql','schema/initial_schema.sql')
    mirror.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='mirror differ'):
        verify_pair(tmp_path,'CONTRACTS/initial_schema.sql','schema/initial_schema.sql')


def test_missing_mirror_is_rejected(tmp_path):
    (tmp_path/'CONTRACTS').mkdir()
    (tmp_path/'CONTRACTS/initial_schema.sql').write_bytes(b'canonical')
    with pytest.raises(ValueError):
        verify_pair(tmp_path,'CONTRACTS/initial_schema.sql','schema/initial_schema.sql')


def test_tampered_catalog_resource_is_rejected(tmp_path):
    # One sample copy plus the unchanged index: all other resources are absent,
    # so the validator must reject the copy rather than credit a fake PASS.
    base=tmp_path/'src/studio/organization/catalog'
    base.mkdir(parents=True)
    shutil.copy(ROOT/'src/studio/organization/catalog/resource-index.json',base/'resource-index.json')
    with pytest.raises(ValueError):
        verify_catalog(tmp_path)
