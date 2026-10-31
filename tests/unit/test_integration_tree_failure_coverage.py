"""Actual M local tree artifact lifecycle: immutable replay and byte-exact QA/remote gates."""
from types import SimpleNamespace
import pytest
from studio.app.errors import StudioError
from studio.app.integration import CanonicalTree, tree_digest
from studio.app.intake import digest


def handoff(root, changes=(), **kwargs):
    return SimpleNamespace(base_revision_id=kwargs.get('base_revision_id',root.current_revision()),changes=changes)


def change(path='src/task.txt', old=None, new=b'value'):
    return SimpleNamespace(path=path,prior_sha256=digest(old) if old is not None else None,
                           new_sha256=digest(new) if new is not None else None,content=new)


class QA:
    def __init__(self,verified=False):self.verified=verified;self.calls=0
    def verify(self,**kwargs):self.calls+=1;return self.verified

class Remote:
    def __init__(self,verified=False):self.verified=verified;self.calls=0
    def verify_exact(self,**kwargs):self.calls+=1;return self.verified


def fail(code,fn):
    with pytest.raises(StudioError) as e:fn()
    assert e.value.code==code


@pytest.mark.parametrize('project', ['', '.', '..', 'p/other', 'p\\other'])
def test_unsafe_project_identifiers_blocked(tmp_path,project):
    fail('invalid',lambda:CanonicalTree(tmp_path,project))


def test_empty_revision_and_invalid_readback(tmp_path):
    root=CanonicalTree(tmp_path,'p')
    assert root.current_revision()==tree_digest({})
    assert root.read_revision(root.current_revision())=={}
    fail('invalid',lambda:root.read_revision('bad'))
    fail('missing',lambda:root.read_revision('a'*64))
    assert CanonicalTree(tmp_path,'p').current_revision()==tree_digest({})


def test_stage_requires_project_intent_submissions_and_author(tmp_path):
    root=CanonicalTree(tmp_path,'p')
    good=('submission','author',handoff(root,[change()]))
    fail('forbidden',lambda:root.stage(intent_id='x',project_id='other',submissions=[good]))
    fail('invalid',lambda:root.stage(intent_id='../bad',project_id='p',submissions=[good]))
    fail('invalid',lambda:root.stage(intent_id='x',project_id='p',submissions=[]))
    fail('invalid',lambda:root.stage(intent_id='x',project_id='p',submissions=[('', 'a',handoff(root))]))
    fail('invalid',lambda:root.stage(intent_id='x',project_id='p',submissions=[('s','',handoff(root))]))


def test_source_merge_rejects_stale_baseline_duplicate_and_overlapping_files(tmp_path):
    root=CanonicalTree(tmp_path,'p')
    fail('stale_revision',lambda:root.stage(intent_id='bad',project_id='p',submissions=[
        ('s','a',handoff(root,[change()],base_revision_id='a'*64))]))
    fail('conflict',lambda:root.stage(intent_id='bad',project_id='p',submissions=[
        ('s','a',handoff(root,[change()])),('t','b',handoff(root,[change()]))]))
    fail('invalid',lambda:root.stage(intent_id='bad',project_id='p',submissions=[
        ('s','a',handoff(root,[change()])),('s','b',handoff(root,[change('src/new.txt')]))]))
    fail('conflict',lambda:root.stage(intent_id='bad',project_id='p',submissions=[
        ('s','a',handoff(root,[change(old=b'outdated')]))]))
    altered=change();altered.new_sha256='b'*64
    fail('invalid',lambda:root.stage(intent_id='bad',project_id='p',submissions=[
        ('s','a',handoff(root,[altered]))]))


def test_immutable_staging_and_negative_promote_checks(tmp_path):
    root=CanonicalTree(tmp_path,'p')
    base=root.current_revision();h=handoff(root,[change()])
    staged=root.stage(intent_id='intent',project_id='p',submissions=[('submission','author',h)])
    assert root.inspect('intent').revision_id==staged.revision_id
    assert root.stage(intent_id='intent',project_id='p',submissions=[('submission','author',h)])==staged
    fail('conflict',lambda:root.stage(intent_id='intent',project_id='p',submissions=[
        ('other','author',h)]))
    qa=QA();remote=Remote()
    fail('qa_failed',lambda:root.promote(intent_id='intent',review_ids=(),qa=qa,
                                         readback=remote,expected_revision_id=base))
    fail('qa_failed',lambda:root.promote(intent_id='intent',review_ids=('review',),qa=qa,
                                         readback=remote,expected_revision_id=base))
    assert remote.calls==0
    qa.verified=True
    fail('unknown_effect',lambda:root.promote(intent_id='intent',review_ids=('review',),qa=qa,
                                              readback=remote,expected_revision_id=base))
    remote.verified=True
    revision=root.promote(intent_id='intent',review_ids=('review',),qa=qa,
                          readback=remote,expected_revision_id=base)
    assert revision==staged.revision_id
    assert root.read_revision(revision)=={'src/task.txt':b'value'}
    fail('stale_revision',lambda:root.promote(intent_id='intent',review_ids=('review',),qa=qa,
                                             readback=remote,expected_revision_id=base))


def test_staged_bytes_corruption_and_verified_recovery(tmp_path):
    root=CanonicalTree(tmp_path,'p')
    staged=root.stage(intent_id='intent',project_id='p',submissions=[
        ('submission','author',handoff(root,[change()]))])
    candidate=root.stages/'intent'/'tree'/'src'/'task.txt'
    candidate.write_bytes(b'tampered')
    fail('invalid',lambda:root.inspect('intent'))
    candidate.write_bytes(b'value')
    fail('invalid',lambda:root.recover_local_pointer('a'*64,staged.tree))
    root.recover_local_pointer(staged.revision_id,staged.tree)
    assert root.current_revision()==staged.revision_id
    assert root.read_revision(staged.revision_id)==staged.tree
    (root.revisions/staged.revision_id/'src/task.txt').write_bytes(b'tampered')
    fail('invalid',lambda:root.recover_local_pointer(staged.revision_id,staged.tree))