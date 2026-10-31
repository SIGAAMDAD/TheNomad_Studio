"""Installed capabilities refuse tampered bytes, duplicate paths and unpinned tools."""
import hashlib
import json

import pytest

from studio.integrations.common import IntegrationError, ProjectBinding
from studio.integrations.toolchain import PackageVerifier


def digest(body):
    return hashlib.sha256(body).hexdigest()


def setup(tmp_path):
    root=tmp_path/'package';root.mkdir()
    (root/'tool').write_bytes(b'installed-tool')
    (root/'input.json').write_bytes(b'{}')
    (root/'output.json').write_bytes(b'{}')
    resources=[{'path':path,'sha256':digest((root/path).read_bytes()),
                'byte_size':(root/path).stat().st_size}
               for path in ('tool','input.json','output.json')]
    manifest={'manifest_version':'1','resources':resources,
              'entrypoints':[{'executable_path':'tool','args_schema_path':'input.json',
                              'output_schema_path':'output.json',
                              'executable_digest':resources[0]['sha256'],
                              'args_schema_digest':resources[1]['sha256'],
                              'output_schema_digest':resources[2]['sha256']}]}
    return root,manifest


def verify(root,manifest,*,installed_hash=None):
    raw=json.dumps(manifest).encode()
    return PackageVerifier(ProjectBinding('project','account')).verify(
        project_id='project',root=root,manifest_bytes=raw,
        expected_digest=installed_hash or digest(raw))


def test_manifest_and_installed_artifacts_verify_exact_bytes(tmp_path):
    root,manifest=setup(tmp_path)
    assert verify(root,manifest)==manifest
    with pytest.raises(IntegrationError):
        verify(root,manifest,installed_hash='0'*64)
    with pytest.raises(IntegrationError):
        PackageVerifier(ProjectBinding('project','account')).verify(
            project_id='another',root=root,manifest_bytes=b'{}',expected_digest=digest(b'{}'))


@pytest.mark.parametrize('edit',[
    lambda m:m.update(manifest_version='0'),
    lambda m:m.update(resources='not-list'),
    lambda m:m.update(entrypoints='not-list'),
    lambda m:m['resources'].append({'incomplete':True}),
    lambda m:m['resources'][0].update(byte_size=999),
    lambda m:m['resources'][0].update(sha256='a'*64),
    lambda m:m['resources'].append(dict(m['resources'][0])),
    lambda m:m['entrypoints'][0].update(executable_path='not-in-resources'),
    lambda m:m['entrypoints'][0].update(executable_digest='0'*64),
])
def test_rejects_malformed_package_metadata_and_unpinned_tools(tmp_path,edit):
    root,manifest=setup(tmp_path)
    edit(manifest)
    with pytest.raises(IntegrationError):
        verify(root,manifest)


def test_rejects_absolute_and_traversal_paths(tmp_path):
    root,manifest=setup(tmp_path)
    manifest['resources'][0]['path']='../outside'
    with pytest.raises(IntegrationError):
        verify(root,manifest)
    manifest['resources'][0]['path']=str(root/'tool')
    with pytest.raises(IntegrationError):
        verify(root,manifest)


def test_rejects_invalid_manifest_json(tmp_path):
    root,_=setup(tmp_path)
    with pytest.raises(IntegrationError):
        PackageVerifier(ProjectBinding('project','account')).verify(
            project_id='project',root=root,manifest_bytes=b'not-json',
            expected_digest=digest(b'not-json'))