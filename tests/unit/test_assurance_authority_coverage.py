"""E domain source guards for independent review and verified QA inputs."""
from dataclasses import dataclass
import hashlib

import pytest

from studio.assurance.service import (
    AssuranceError, AssuranceService, _required, _sha, _verify_bytes,
    _assert_fields, _canonical, _digest,
)


@pytest.mark.parametrize('bad', (None, '', '  ', 7, [], {}))
def test_assurance_requires_actual_text(bad):
    with pytest.raises(AssuranceError) as exc:
        _required(bad, 'required')
    assert exc.value.code == 'invalid'


@pytest.mark.parametrize('bad', (None, '', 'A'*64, 'a'*63, 'z'*64))
def test_assurance_rejects_unverified_digests(bad):
    with pytest.raises(AssuranceError) as exc:
        _sha(bad, 'evidence')
    assert exc.value.code == 'invalid'


def test_assurance_canonical_evidence_and_exact_bytes():
    value = {'b': 2, 'a': 1}
    raw = _canonical(value)
    assert raw == b'{"a":1,"b":2}'
    digest = _digest(raw)
    assert _sha(digest,'digest') == digest
    assert _verify_bytes(raw, digest, 'evidence') is None
    for payload in ('incorrect', b'tampered', None):
        with pytest.raises(AssuranceError) as exc:
            _verify_bytes(payload, digest, 'evidence')
        assert exc.value.code == 'conflict'


@pytest.mark.parametrize('bad', ({}, {'a':1,'z':2}, None, [], {'a':1,'b':2}))
def test_assurance_frozen_request_shapes(bad):
    with pytest.raises(AssuranceError) as exc:
        _assert_fields(bad, {'a'}, {'optional'})
    assert exc.value.code == 'invalid'
    assert _assert_fields({'a':1,'optional':True}, {'a'}, {'optional'}) is None


class Tx:
    def __init__(self, *, author=True, grant=True, author_kind='human',
                 same_principal=False, lineage=True, reviewer_root=None,
                 same_root=False, participant=True):
        self.author, self.grant = author, grant
        self.author_kind, self.same_principal = author_kind, same_principal
        self.lineage = lineage
        self.reviewer_root, self.same_root, self.participant = reviewer_root, same_root, participant

    def one(self, sql, args=()):
        if 'FROM worker_shifts sh JOIN worker_conversations w' in sql:
            return {'author_principal_id': 'reviewer' if self.same_principal else 'author', 'worker_id':'worker'} if self.author else None
        if 'SELECT kind FROM principals' in sql:
            return {'kind':self.author_kind}
        if 'FROM project_grants' in sql:
            return {'ok':1} if self.grant else None
        if 'FROM worker_lineages' in sql and 'JOIN worker_conversations' in sql:
            return {'ok':1} if self.participant else None
        if 'FROM worker_lineages' in sql:
            if not self.lineage:
                return None
            return {'root_worker_id': 'author-root' if args[-1]=='worker' or self.same_root else 'reviewer-root'}
        raise AssertionError('unexpected SQL query: '+sql)


@pytest.mark.parametrize('actor', [None, {}, {'allowed_project_ids':['other']},
    {'allowed_project_ids':['p'],'principal_id':''},
    {'allowed_project_ids':['p'],'principal_id':'reviewer','issuer':''},
    {'allowed_project_ids':['p'],'principal_id':'reviewer','issuer':'auth','trusted_proof_ref':''}])
def test_assurance_scope_and_identity_fail_closed(actor):
    with pytest.raises(AssuranceError):
        AssuranceService._authorized(Tx(),actor,'p')


def test_assurance_requires_current_project_grant():
    trusted={'allowed_project_ids':['p'],'principal_id':'reviewer','issuer':'auth','trusted_proof_ref':'signed'}
    with pytest.raises(AssuranceError,match='grant'):
        AssuranceService._authorized(Tx(grant=False), trusted,'p')
    assert AssuranceService._authorized(Tx(),trusted,'p') == 'reviewer'


@pytest.mark.parametrize('kwargs', [
    {'author':False}, {'same_principal':True}, {'grant':False},
    {'lineage':False}, {'author_kind':'worker'},
    {'reviewer_root':'reviewer-root','same_root':True},
    {'reviewer_root':'reviewer-root','participant':False},
])
def test_independent_reviewer_rejects_missing_provenance(kwargs):
    tx=Tx(**kwargs)
    with pytest.raises(AssuranceError) as caught:
        AssuranceService._independent(tx,'p','shift','reviewer',tx.reviewer_root)
    assert caught.value.code in ('forbidden','conflict')


def test_distinct_human_and_independent_worker_lineages_are_valid():
    human=AssuranceService._independent(Tx(),'p','shift','reviewer',None)
    assert human['author_shift_id']=='shift'
    assert human['reviewer_root_worker_id'] is None
    reviewer=AssuranceService._independent(Tx(reviewer_root='reviewer-root',author_kind='worker'),
                                          'p','shift','reviewer','reviewer-root')
    assert reviewer['reviewer_root_worker_id']=='reviewer-root'
    assert reviewer['author_root_worker_id']=='author-root'