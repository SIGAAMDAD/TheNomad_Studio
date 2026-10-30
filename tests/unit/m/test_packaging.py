"""Scoped, deterministic and compressed source contribution tests."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from studio.app.intake import Roster


def packer():
    path = Path(__file__).resolve().parents[3] / 'scripts/package_contribution.py'
    spec = spec_from_file_location('contribution_packer', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLAN = Path(os.environ.get('STUDIO_CONTRIBUTION_PLAN',
             Path(__file__).resolve().parents[3] / 'tests/fixtures/source_contributions'))


def test_contribution_compression_and_reproducibility(tmp_path):
    source = tmp_path / 'source'
    (source / 'src/studio/app').mkdir(parents=True)
    (source / 'src/studio/app/component.py').write_text('function = 12\n' * 900)
    output_a, output_b = tmp_path / 'a.zip', tmp_path / 'b.zip'
    manifest_a, manifest_b = tmp_path / 'a.json', tmp_path / 'b.json'
    results_a = packer().package(source, PLAN, output_a, manifest_a, owner='M', base_revision='a'*40)
    results_b = packer().package(source, PLAN, output_b, manifest_b, owner='M', base_revision='a'*40)
    assert results_a == results_b
    assert output_a.read_bytes() == output_b.read_bytes()
    assert results_a['compression_ratio'] < 0.10
    assert all(x.compress_type == ZIP_DEFLATED for x in ZipFile(output_a).infolist())
    assert set(ZipFile(output_a).namelist()) == {'src/studio/app/component.py'}
    assert Roster.load(PLAN).verify_handoff(output_a, json.loads(manifest_a.read_text())).group == 'M'


def test_changed_only_and_deletion_manifest(tmp_path):
    base, source = tmp_path / 'baseline', tmp_path / 'source'
    for root in (base, source):
        (root / 'src/studio/app').mkdir(parents=True)
    (base / 'src/studio/app/unchanged.py').write_text('unchanged')
    (source / 'src/studio/app/unchanged.py').write_text('unchanged')
    (base / 'src/studio/app/deleted.py').write_text('old')
    (base / 'src/studio/app/changed.py').write_text('old')
    (source / 'src/studio/app/changed.py').write_text('new')
    archive, manifest = tmp_path / 'update.zip', tmp_path / 'update.json'
    result = packer().package(source, PLAN, archive, manifest, owner='M', baseline=base,
                              base_revision='f'*40)
    assert result['changed_paths'] == 2
    assert result['archived_paths'] == 1
    assert ZipFile(archive).namelist() == ['src/studio/app/changed.py']
    data = json.loads(manifest.read_text())
    assert data['files']['src/studio/app/deleted.py']['after'] is None
    assert Roster.load(PLAN).verify_handoff(archive, data).changes


def test_rejects_unowned_file_and_symlink(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'foreign.py').write_text('not owned')
    with pytest.raises(ValueError, match='unowned'):
        packer().package(source, PLAN, tmp_path/'out.zip', tmp_path/'out.json', owner='M', base_revision='a'*40)
    (source/'foreign.py').unlink()
    (source/'src/studio/app').mkdir(parents=True)
    (source/'src/studio/app/link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symlinks'):
        packer().package(source, PLAN, tmp_path/'out.zip', tmp_path/'out.json', owner='M', base_revision='a'*40)


def test_owner_and_baseline_required(tmp_path):
    src = tmp_path / 'source';src.mkdir()
    with pytest.raises(ValueError, match='unknown contribution owner'):
        packer().package(src, PLAN, tmp_path/'out.zip', tmp_path/'out.json', owner='X', base_revision='a'*40)
    with pytest.raises(ValueError, match='baseline revision'):
        packer().package(src, PLAN, tmp_path/'out.zip', tmp_path/'out.json', owner='M', base_revision='')
