from __future__ import annotations

from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec
import json
from zipfile import ZipFile

from studio.app.intake import Roster


def test_m_only_owned_package_and_reproducible(tmp_path, monkeypatch):
    source=tmp_path/'source';(source/'src/studio/app').mkdir(parents=True)
    (source/'src/studio/app/test.py').write_text('value=4\n')
    spec=spec_from_file_location('packaging',Path(__file__).resolve().parents[3]/'scripts/package_wg_m.py')
    mod=module_from_spec(spec);spec.loader.exec_module(mod)
    packet=Path(__import__('os').environ.get('NOMAD_R5_PACKET',Path(__file__).resolve().parents[3]))
    zip_a=tmp_path/'a.zip';zip_b=tmp_path/'b.zip'
    manifest_a=tmp_path/'a.json';manifest_b=tmp_path/'b.json'
    x=mod.package(source,packet,zip_a,manifest_a)
    y=mod.package(source,packet,zip_b,manifest_b)
    assert x==y
    assert zip_a.read_bytes()==zip_b.read_bytes()
    assert set(ZipFile(zip_a).namelist())=={'src/studio/app/test.py'}
    assert Roster.load(packet).verify_handoff(zip_a,json.loads(manifest_a.read_text())).group=='M'
