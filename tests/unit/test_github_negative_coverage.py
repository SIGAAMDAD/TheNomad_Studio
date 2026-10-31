"""Production G boundaries: malformed remote bytes, immutable effects and cursor safety."""
import base64
import hashlib

import pytest

from studio.integrations.common import (
    IntegrationError, ProviderFailure, RemotePreflightUnavailable,
    RemoteUnknown, ProjectBinding,
)
from studio.integrations.github import GitHub, GitHubChannel, RemoteFile


HEAD = 'a' * 40
BEFORE = 'b' * 40


def adapter():
    return GitHub(object(), lambda: 'token',
                  GitHubChannel('project', 'owner', 'repo', 'comms'),
                  ProjectBinding('project', 'account'))


@pytest.mark.parametrize('values', [
    ('project', '.', 'repo', 'comms'),
    ('project', 'owner', '..', 'comms'),
    ('project', 'owner', 'repo', '../bad'),
    ('project', 'owner', 'repo', 'bad.lock'),
])
def test_channel_rejects_untrusted_remote_names(values):
    with pytest.raises(IntegrationError):
        GitHubChannel(*values)


def test_channel_rejects_cross_project_root_and_unknown_path_kind():
    with pytest.raises(IntegrationError):
        GitHubChannel('project', 'owner', 'repo', 'comms', root='.nomad/relays/other')
    with pytest.raises(IntegrationError):
        adapter().channel.path('arbitrary', 'worker', 'id')


def test_ref_readback_cannot_accept_malformed_object():
    g=adapter()
    g._request=lambda *_args,**_kwargs: {'object': 'bad'}
    with pytest.raises(ProviderFailure):
        g.branch_tip()


def test_tree_rejects_missing_commit_truncated_tree_and_invalid_entries():
    g=adapter()
    g._request=lambda method,path,*_args,**_kwargs: {}
    with pytest.raises(ProviderFailure):
        g._tree(HEAD)
    def truncated(method,path,*args,**kwargs):
        if '/git/commits/' in path:
            return {'tree': {'sha': BEFORE}}
        return {'tree': [], 'truncated': True}
    g._request=truncated
    with pytest.raises(RemoteUnknown):
        g._tree(HEAD)
    def malformed(method,path,*args,**kwargs):
        if '/git/commits/' in path:
            return {'tree': {'sha': BEFORE}}
        return {'truncated': False, 'tree': [{'type':'blob','path':'.nomad/relays/project/mail/a/b.json','sha':5}]}
    g._request=malformed
    with pytest.raises(ProviderFailure):
        g._tree(HEAD)


def test_tree_filters_other_projects_and_nonblob_entries():
    g=adapter()
    def contents(_method,path,*_args,**_kwargs):
        if '/git/commits/' in path:
            return {'tree': {'sha': BEFORE}}
        return {'truncated': False, 'tree': [
            {'type':'tree','path':'.nomad/relays/project/mail','sha':BEFORE},
            {'type':'blob','path':'.nomad/relays/other/mail/a.json','sha':BEFORE},
            {'type':'blob','path':'.nomad/relays/project/mail/a/b.json','sha':HEAD}]}
    g._request=contents
    assert g._tree(HEAD)=={'.nomad/relays/project/mail/a/b.json':HEAD}


def test_branch_history_rewrite_is_unknown_and_identical_revision_is_noop():
    g=adapter()
    calls=[]
    g._request=lambda *_args,**_kwargs: (calls.append(True) or {'status':'behind'})
    g._is_descendant(HEAD,HEAD)
    assert not calls
    with pytest.raises(RemoteUnknown):
        g._is_descendant(BEFORE,HEAD)


def test_exact_file_returns_none_on_404_and_rejects_untrusted_paths():
    g=adapter()
    def missing(*args,**kwargs):
        raise ProviderFailure('not found',status=404)
    g._request=missing
    path=g.channel.path('mail','worker','command')
    assert g._read_at(path,HEAD) is None
    with pytest.raises(IntegrationError):
        g._read_at('untrusted/path',HEAD)


@pytest.mark.parametrize('obj',[
    {'type':'dir','encoding':'base64','content':''},
    {'type':'file','encoding':'utf-8','content':''},
    {'type':'file','encoding':'base64','content':[],'sha':HEAD},
    {'type':'file','encoding':'base64','content':'','sha':HEAD},
])
def test_pinned_readback_rejects_malformed_file_or_blob(obj):
    g=adapter()
    g._request=lambda *_args,**_kwargs: obj
    path=g.channel.path('handoffs','worker','checkpoint')
    with pytest.raises((ProviderFailure,RemoteUnknown)):
        g._read_at(path,HEAD)


def test_pinned_readback_enforces_expected_git_tree_blob():
    g=adapter()
    body=b'verified'
    blob=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()
    g._request=lambda *_args,**_kwargs:{'type':'file','encoding':'base64',
              'content':base64.b64encode(body).decode(), 'sha':blob}
    path=g.channel.path('reports','worker','one')
    with pytest.raises(RemoteUnknown):
        g._read_at(path,HEAD,'0'*40)
    assert g._read_at(path,HEAD,blob).body_sha256==hashlib.sha256(body).hexdigest()


def test_remote_scan_rejects_invalid_bounds_and_changed_branch_tip():
    g=adapter()
    g.branch_tip=lambda:HEAD
    with pytest.raises(IntegrationError):
        g.scan_since(None,limit=0)
    with pytest.raises(IntegrationError):
        g.scan_since(None,limit=501)
    with pytest.raises(RemoteUnknown):
        g.scan_since(None,limit=1,expected_head=BEFORE)


def test_remote_scan_does_not_ack_unrecognized_or_missing_file():
    g=adapter()
    g.branch_tip=lambda:HEAD
    g._tree=lambda _: {'.nomad/relays/project/unknown.bin': BEFORE}
    with pytest.raises(RemoteUnknown):
        g.scan_since(None,limit=10)
    g._tree=lambda _: {g.channel.path('reports','worker','one'): BEFORE}
    g._scan_cache_key=None
    g._read_at=lambda *_args,**_kwargs: None
    with pytest.raises(RemoteUnknown):
        g.scan_since(None,limit=10)
    with pytest.raises(IntegrationError):
        g.scan_since(None,limit=10,offset=50)


def test_publication_fails_closed_before_intent_or_on_hash_mismatch():
    g=adapter()
    body=b'{}'
    with pytest.raises(IntegrationError):
        g.publish(g.channel.path('reports','worker','one'),body,
                  expected_digest=hashlib.sha256(body).hexdigest(),effect_intent_recorded=False)
    with pytest.raises(IntegrationError):
        g.publish(g.channel.path('reports','worker','one'),body,
                  expected_digest='0'*64,effect_intent_recorded=True)


def test_unknown_preflight_does_not_issue_put():
    g=adapter()
    g._validate_path=lambda *_:None
    g.observe_exact=lambda *_a,**_kw: (_ for _ in ()).throw(RemoteUnknown('offline'))
    g._request=lambda *_a,**_kw: (_ for _ in ()).throw(AssertionError('PUT should never happen'))
    body=b'{}'
    with pytest.raises(RemotePreflightUnavailable):
        g.publish(g.channel.path('reports','worker','one'),body,
                  expected_digest=hashlib.sha256(body).hexdigest(),effect_intent_recorded=True)


def test_existing_remote_file_with_wrong_digest_cannot_be_replaced():
    g=adapter()
    g._validate_path=lambda *_:None
    path=g.channel.path('reports','worker','one')
    g.observe_exact=lambda *_a,**_kw:RemoteFile('project',path,HEAD,'0'*64,b'changed')
    g._request=lambda *_a,**_kw: (_ for _ in ()).throw(AssertionError('overwrite forbidden'))
    body=b'{}'
    with pytest.raises(IntegrationError):
        g.publish(path,body,expected_digest=hashlib.sha256(body).hexdigest(),effect_intent_recorded=True)


def test_unverified_put_receipt_never_marks_publication_verified():
    g=adapter()
    g._validate_path=lambda *_:None
    g.observe_exact=lambda *_a,**_kw:None
    g._request=lambda *_a,**_kw: {'commit': {'sha':HEAD}}
    body=b'{}'
    with pytest.raises(RemoteUnknown):
        g.publish(g.channel.path('reports','worker','one'),body,
                  expected_digest=hashlib.sha256(body).hexdigest(),effect_intent_recorded=True)