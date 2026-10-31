from __future__ import annotations

from pathlib import Path
import pytest

from studio.app.errors import StudioError
from studio.app.intake import Change, Handoff, digest
from studio.app.integration import CanonicalTree, tree_digest
from studio.app.verification import IndependentReceiptGate, QARecord, RemoteTreeReadbackGate

PLAN = 'a' * 64


def submission(group, base, path, before, after):
    return Handoff(group, PLAN, base,
                   (Change(path, before, digest(after) if after is not None else None, after),), ())


class Reviews:
    def __init__(self, records): self.records = records
    def get_review(self, *, project_id, review_id):
        return self.records.get(review_id)


class Remote:
    def __init__(self, tree): self.tree = tree
    def read_revision(self, *, project_id, revision_id): return self.tree


def acceptance(candidate):
    reviews = {}
    ids = []
    for i, (submission_id, author) in enumerate(zip(candidate.submission_ids, candidate.author_ids)):
        rid = f'review-{i}'
        reviews[rid] = QARecord(rid, candidate.project_id, candidate.revision_id,
                                submission_id, author, f'reviewer-{i}', 'pass',
                                'evidence', True, True)
        ids.append(rid)
    return tuple(ids), IndependentReceiptGate(Reviews(reviews)), RemoteTreeReadbackGate(Remote(candidate.tree))


def test_new_empty_canonical_tree_is_stable(tmp_path):
    tree = CanonicalTree(tmp_path, 'first')
    assert tree.current_revision() == tree_digest({})
    assert tree.read_revision(tree.current_revision()) == {}
    assert CanonicalTree(tmp_path, 'first').current_revision() == tree.current_revision()


def test_multiworker_nonoverlap_then_cas_promotion(tmp_path):
    tree = CanonicalTree(tmp_path, 'one')
    before = tree.current_revision()
    a = submission('A', before, 'src/studio/governance/rules.py', None, b'a')
    m = submission('M', before, 'src/studio/app/run.py', None, b'm')
    candidate = tree.stage(intent_id='intent1', project_id='one',
                           submissions=[('sub-a','author-a',a),('sub-m','author-m',m)])
    assert tree.current_revision() == before  # staging is not promotion
    assert tree.inspect('intent1').tree == candidate.tree
    ids, qa, remote = acceptance(candidate)
    promoted = tree.promote(intent_id='intent1', review_ids=ids, qa=qa, readback=remote,
                            expected_revision_id=before)
    assert promoted == candidate.revision_id
    assert tree.current_revision() == promoted
    assert tree.read_revision(promoted)['src/studio/app/run.py'] == b'm'
    assert CanonicalTree(tmp_path, 'one').current_revision() == promoted


def test_overlapping_source_rejected(tmp_path):
    tree = CanonicalTree(tmp_path, 'one')
    before = tree.current_revision()
    a = submission('A', before, 'same.py', None, b'a')
    b = submission('B', before, 'same.py', None, b'b')
    with pytest.raises(StudioError) as e:
        tree.stage(intent_id='collision', project_id='one', submissions=[('a','x',a),('b','y',b)])
    assert e.value.code == 'conflict'


def test_stale_base_rejected(tmp_path):
    tree = CanonicalTree(tmp_path, 'one')
    bad = submission('M', 'bad' * 20, 'one.py', None, b'new')
    with pytest.raises(StudioError) as e:
        tree.stage(intent_id='bad', project_id='one', submissions=[('a','x',bad)])
    assert e.value.code == 'stale_revision'


def test_old_sha_conflict_rejected(tmp_path):
    tree = CanonicalTree(tmp_path, 'one')
    before = tree.current_revision()
    h = submission('M', before, 'src/studio/app/m.py', 'f'*64, b'new')
    with pytest.raises(StudioError) as e:
        tree.stage(intent_id='bad', project_id='one', submissions=[('a','x',h)])
    assert e.value.code == 'conflict'


def test_unverified_qa_blocks_promotion(tmp_path):
    tree = CanonicalTree(tmp_path, 'one')
    before = tree.current_revision()
    c = tree.stage(intent_id='candidate', project_id='one',
                   submissions=[('a','author-a',submission('M',before,'f.py',None,b'content'))])
    _, _, remote = acceptance(c)
    bad_qa = IndependentReceiptGate(Reviews({}))
    with pytest.raises(StudioError) as e:
        tree.promote(intent_id='candidate', review_ids=('invented-review',), qa=bad_qa,
                     readback=remote, expected_revision_id=before)
    assert e.value.code == 'qa_failed'
    assert tree.current_revision() == before


def test_wrong_remote_bytes_blocks_promotion(tmp_path):
    tree = CanonicalTree(tmp_path, 'one')
    before = tree.current_revision()
    c = tree.stage(intent_id='candidate', project_id='one',
                   submissions=[('a','author-a',submission('M',before,'f.py',None,b'content'))])
    ids, qa, _ = acceptance(c)
    with pytest.raises(StudioError) as e:
        tree.promote(intent_id='candidate', review_ids=ids, qa=qa,
                     readback=RemoteTreeReadbackGate(Remote({'f.py':b'other'})),
                     expected_revision_id=before)
    assert e.value.code == 'unknown_effect'
    assert tree.current_revision() == before


def test_qa_author_is_reviewer_rejected(tmp_path):
    tree = CanonicalTree(tmp_path, 'one')
    before = tree.current_revision()
    c = tree.stage(intent_id='candidate', project_id='one',
                   submissions=[('sub','author',submission('M',before,'f.py',None,b'content'))])
    review = QARecord('self', 'one', c.revision_id, 'sub', 'author', 'author',
                      'pass', 'evidence', True, True)
    assert not IndependentReceiptGate(Reviews({'self':review})).verify(
        project_id='one',revision_id=c.revision_id,submission_ids=('sub',),
        authors=('author',),review_ids=('self',))


def test_independent_qa_wrong_revision_rejected(tmp_path):
    record = QARecord('review','one','wrong','sub','author','other','pass','evidence',True,True)
    assert not IndependentReceiptGate(Reviews({'review':record})).verify(
        project_id='one', revision_id='correct', submission_ids=('sub',),
        authors=('author',), review_ids=('review',))


def test_cannot_promote_after_pointer_advances(tmp_path):
    tree = CanonicalTree(tmp_path, 'one')
    base = tree.current_revision()
    one = tree.stage(intent_id='one',project_id='one',submissions=[
        ('a','author',submission('M',base,'a.py',None,b'a'))])
    two = tree.stage(intent_id='two',project_id='one',submissions=[
        ('b','author',submission('M',base,'b.py',None,b'b'))])
    ids,qa,remote = acceptance(one)
    tree.promote(intent_id='one',review_ids=ids,qa=qa,readback=remote,expected_revision_id=base)
    ids2,qa2,remote2 = acceptance(two)
    with pytest.raises(StudioError) as e:
        tree.promote(intent_id='two',review_ids=ids2,qa=qa2,readback=remote2,expected_revision_id=base)
    assert e.value.code == 'stale_revision'


def test_recover_mirror_from_verified_bytes_only(tmp_path):
    tree=CanonicalTree(tmp_path,'one')
    good={'a.py':b'a'}
    rev=tree_digest(good)
    with pytest.raises(StudioError):
        tree.recover_local_pointer(rev, {'a.py':b'tampered'})
    tree.recover_local_pointer(rev,good)
    assert tree.current_revision()==rev


def test_projects_are_isolated(tmp_path):
    one=CanonicalTree(tmp_path,'alpha')
    two=CanonicalTree(tmp_path,'beta')
    assert one.root!=two.root
    with pytest.raises(StudioError) as e:
        one.stage(intent_id='bad', project_id='beta', submissions=[
            ('a','author',submission('M',one.current_revision(),'a.py',None,b'a'))])
    assert e.value.code=='forbidden'


def test_declared_empty_tree_baseline_symbol_supported_only_initially(tmp_path):
    tree=CanonicalTree(tmp_path,'one')
    empty=tree.current_revision()
    symbolic=submission('M','EMPTY_NEW_SOURCE_TREE','src/studio/app/init.py',None,b'init')
    candidate=tree.stage(intent_id='first',project_id='one',submissions=[('s','author',symbolic)])
    ids,qa,remote=acceptance(candidate)
    tree.promote(intent_id='first',review_ids=ids,qa=qa,readback=remote,expected_revision_id=empty)
    with pytest.raises(StudioError) as e:
        tree.stage(intent_id='second',project_id='one',submissions=[('s2','author',symbolic)])
    assert e.value.code=='stale_revision'
