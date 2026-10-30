from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from studio.app.errors import StudioError
from studio.app.intake import Roster, GROUPS, digest, normalized

PACKET = Path(__import__('os').environ.get('NOMAD_R5_PACKET', Path(__file__).resolve().parents[3]))


def roster() -> Roster:
    return Roster.load(PACKET)


def archive(path: str, data: bytes = b'new', *, attrs: int | None = None) -> io.BytesIO:
    b = io.BytesIO()
    with ZipFile(b, 'w') as z:
        info = ZipInfo(path)
        if attrs is not None:
            info.create_system = 3
            info.external_attr = attrs << 16
        z.writestr(info, data)
    b.seek(0)
    return b


def meta(r: Roster, group='M', path='src/studio/app/engine.py', data=b'new', old=None):
    return {'group': group, 'plan_sha256': r.plan_sha256,
            'base_revision_id': 'a' * 64,
            'files': {path: {'before': old, 'after': digest(data)}}, 'tests': []}


def validate(r, tmp_path, metadata, archive_content):
    z = tmp_path / 'submission.zip'
    z.write_bytes(archive_content.getvalue())
    return r.verify_handoff(z, metadata)


def test_r5_frozen_baseline_is_exact():
    r = roster()
    assert len(r.paths) == 13
    assert set(r.paths) == set(GROUPS)
    assert r.plan_sha256 == '8d68e255008369a299c66ca1a0c253e60faecb2ef46415ad09cbb9b38b41d09b'


def test_real_intake_allows_owned_source(tmp_path):
    r = roster()
    verified = validate(r, tmp_path, meta(r), archive('src/studio/app/engine.py'))
    assert verified.changes[0].content == b'new'
    assert verified.group == 'M'


@pytest.mark.parametrize('badpath', [
    '../escape.txt', '/absolute.txt', 'src/studio/app/../governance/pwn.py',
    'src/studio/app//pwn.py', 'src/studio/app/./pwn.py',
    'src\\studio\\app\\pwn.py', 'C:/evil.py',
])
def test_reject_path_traversal(tmp_path, badpath):
    r = roster()
    with pytest.raises(StudioError):
        validate(r, tmp_path, meta(r, path=badpath), archive(badpath))


@pytest.mark.parametrize('forbidden', ['src/studio/governance/pwn.py',
                                       'src/studio/app_evil/sneak.py',
                                       'tests/unit/l/pwn.py', 'CONTRACTS/openapi.json'])
def test_reject_foreign_worker_source(tmp_path, forbidden):
    r = roster()
    with pytest.raises(StudioError) as e:
        validate(r, tmp_path, meta(r, path=forbidden), archive(forbidden))
    assert e.value.code == 'forbidden'


def test_reject_digest_mismatch_and_plan_mismatch(tmp_path):
    r = roster()
    with pytest.raises(StudioError):
        validate(r, tmp_path, meta(r, data=b'untrue'), archive('src/studio/app/engine.py'))
    m = meta(r)
    m['plan_sha256'] = '0' * 64
    with pytest.raises(StudioError):
        validate(r, tmp_path, m, archive('src/studio/app/engine.py'))


def test_reject_extra_zip_content(tmp_path):
    r = roster()
    with pytest.raises(StudioError):
        validate(r, tmp_path, meta(r), archive('src/studio/app/extra.py'))


def test_reject_symlink(tmp_path):
    import stat
    r = roster()
    with pytest.raises(StudioError):
        validate(r, tmp_path, meta(r), archive('src/studio/app/engine.py', attrs=stat.S_IFLNK | 0o777))


def test_reject_duplicate_casefold_manifest(tmp_path):
    r = roster()
    m = meta(r)
    m['files']['src/studio/app/ENGINE.py'] = m['files']['src/studio/app/engine.py']
    with pytest.raises(StudioError):
        validate(r, tmp_path, m, archive('src/studio/app/engine.py'))


def test_allow_empty_tree_manifest_for_delete(tmp_path):
    r = roster()
    m = {'group':'M','plan_sha256':r.plan_sha256,'base_revision_id':'a'*64,
         'files': {'src/studio/app/old.py': {'before':'b'*64,'after':None}}}
    with ZipFile(tmp_path / 'deletion.zip', 'w'):
        pass
    verified = r.verify_handoff(tmp_path / 'deletion.zip', m)
    assert verified.changes[0].content is None
