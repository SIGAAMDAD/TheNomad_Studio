"""Source readback must prove exact repository, ancestry, path and blob bytes."""
import base64
import hashlib

import pytest

from studio.integrations.common import IntegrationError, ProjectBinding, ProviderFailure, RemoteUnknown
from studio.integrations.source import GitHubSource

HEAD='a'*40
BEFORE='b'*40


def source():
    return GitHubSource(object(),lambda:'token',ProjectBinding('project','account'),
                        'owner','repo','main')


def test_ref_response_requires_valid_object():
    g=source()
    g._request=lambda *_args: {}
    with pytest.raises(ProviderFailure):
        g.ref_sha(project_id='project')


def test_commit_must_be_reachable_from_configured_source_ref():
    g=source()
    g.ref_sha=lambda **kwargs:HEAD
    g._request=lambda *_args: {'status':'diverged'}
    assert g.confirm_reachable(project_id='project',commit_sha=HEAD)==HEAD
    with pytest.raises(IntegrationError):
        g.confirm_reachable(project_id='project',commit_sha=BEFORE)
    g._request=lambda *_args: {'status':'ahead'}
    assert g.confirm_reachable(project_id='project',commit_sha=BEFORE)==HEAD


@pytest.mark.parametrize('path',['', '../outside', '/absolute', 'a//b', 'a/./b', 'a\\b'])
def test_invalid_source_path_rejected_before_network(path):
    g=source()
    g.confirm_reachable=lambda **kwargs:(_ for _ in ()).throw(AssertionError('unexpected call'))
    with pytest.raises(IntegrationError):
        g.read_file(project_id='project',commit_sha=HEAD,path=path)


def test_verified_blob_readback_and_byte_limit():
    g=source()
    g.confirm_reachable=lambda **kwargs:HEAD
    body=b'authoritative-remote-file'
    blob=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()
    g._request=lambda *_args:{'type':'file','encoding':'base64',
                              'content':base64.b64encode(body).decode(),'sha':blob}
    observed=g.read_file(project_id='project',commit_sha=HEAD,path='assets/readme.txt')
    assert observed.body==body
    assert observed.commit_sha==HEAD
    assert observed.body_sha256==hashlib.sha256(body).hexdigest()
    with pytest.raises(IntegrationError):
        g.read_file(project_id='project',commit_sha=HEAD,path='assets/readme.txt',max_bytes=1)


def test_source_rejects_wrong_content_sha_encoding_and_missing_bytes():
    g=source();g.confirm_reachable=lambda **kwargs:HEAD
    g._request=lambda *_args:{'type':'folder'}
    with pytest.raises(ProviderFailure):
        g.read_file(project_id='project',commit_sha=HEAD,path='file.txt')
    g._request=lambda *_args:{'type':'file','encoding':'utf-8','sha':HEAD}
    with pytest.raises(ProviderFailure):
        g.read_file(project_id='project',commit_sha=HEAD,path='file.txt')
    g._request=lambda *_args:{'type':'file','encoding':'base64','sha':HEAD,'content':'!bad-base64!'}
    with pytest.raises(ProviderFailure):
        g.read_file(project_id='project',commit_sha=HEAD,path='file.txt')
    g._request=lambda *_args:{'type':'file','encoding':'base64','sha':HEAD,'content':base64.b64encode(b'bad').decode()}
    with pytest.raises(RemoteUnknown):
        g.read_file(project_id='project',commit_sha=HEAD,path='file.txt')


def test_large_source_blob_resolved_at_same_git_sha():
    g=source();g.confirm_reachable=lambda **kwargs:HEAD
    body=b'large-file'
    blob=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()
    seen=[]
    def request(_method,path):
        seen.append(path)
        if path.startswith('/contents/'):
            return {'type':'file','encoding':'none','sha':blob}
        return {'encoding':'base64','content':base64.b64encode(body).decode()}
    g._request=request
    result=g.read_file(project_id='project',commit_sha=HEAD,path='large.bin')
    assert result.body==body
    assert seen[-1]=='/git/blobs/'+blob