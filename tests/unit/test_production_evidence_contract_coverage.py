"""D evidence ports prove current C grants, true shift receipts and E review bytes."""
import hashlib
import sqlite3
import pytest

from studio.production.service import ProductionFault
from studio.app.production_evidence import (CurrentProductionAuthorization,
    VerifiedProjectBriefs,IndependentQAAcceptance,VerifiedShiftTaskProgress,
    CurrentProductionAuthorizationBuilder)
from studio.storage.workforce import WorkforceStorage


class Tx:
    def __init__(self,one=None,rows=()):self.one_result=one;self.rows=list(rows);self.queries=[]
    def one(self,sql,args=()):self.queries.append(('one',sql,args));return self.one_result
    def all(self,sql,args=()):self.queries.append(('all',sql,args));return self.rows


class Writer(WorkforceStorage):
    def __init__(self,tx):
        super().__init__(sqlite3.connect(':memory:'))
        self.tx=tx
    def read(self,project_id,fn):return fn(self.tx)


def claim(**kwargs):
    x={'principal_id':'owner','issuer':'issuer','trusted_proof_ref':'signed',
       'allowed_project_ids':['p'],'owner_verified':True}
    x.update(kwargs);return x


def fail(code,fn):
    with pytest.raises(ProductionFault) as e:fn()
    assert e.value.code==code


def test_current_authorization_checks_real_db_grants_and_verified_owner():
    tx=Tx(one=None);w=Writer(tx)
    policy=CurrentProductionAuthorization(w,owner_operations=frozenset({'assignTask'}))
    try:
        for bad in (None,{},claim(issuer=''),claim(principal_id=''),
                    claim(allowed_project_ids=['foreign'])):
            fail('forbidden',lambda:policy.require(bad,'p','listTasks'))
        fail('forbidden',lambda:policy.require(claim(),'p','listTasks'))
        tx.one_result={'kind':'human','grant_code':'owner'}
        policy.require(claim(),'p','listTasks')
        fail('requires_owner',lambda:policy.require(claim(owner_verified=False),'p','assignTask'))
        policy.require(claim(),'p','assignTask')
        assert any('project_grants' in sql for _,sql,_ in tx.queries)
    finally:w._connection.close()


def test_verified_feature_brief_must_be_in_C_and_match_G_bytes():
    raw=b'{"brief":"verified"}'
    digest=hashlib.sha256(raw).hexdigest()
    class Original:
        def __init__(self):self.body=raw
        def get(self,*,project_id,digest):return self.body
    tx=Tx();w=Writer(tx);origin=Original()
    try:
        briefs=VerifiedProjectBriefs(w,origin)
        fail('forbidden',lambda:briefs.get_verified(project_id='p',content_sha256=digest))
        tx.one_result={'ok':1}
        assert briefs.get_verified(project_id='p',content_sha256=digest)==raw
        origin.body=b'changed'
        fail('conflict',lambda:briefs.get_verified(project_id='p',content_sha256=digest))
    finally:w._connection.close()


def test_independent_QA_requires_review_and_receipt_for_both_outcomes():
    tx=Tx();qa=IndependentQAAcceptance()
    req=dict(project_id='p',task_id='task',lease_generation=1,evidence_digest='a'*64)
    fail('qa_failed',lambda:qa.require_accepted(tx,**req))
    tx.rows=[{'review_id':'review'}]
    assert qa.require_accepted(tx,**req) is None
    tx.rows=[]
    fail('qa_failed',lambda:qa.require_failed(tx,project_id='p',task_id='task',evidence_digest='a'*64))
    tx.rows=[{'review_id':'failure'}]
    assert qa.require_failed(tx,project_id='p',task_id='task',evidence_digest='a'*64) is None


@pytest.mark.parametrize('state,kind', [('submitted','completed'),('failed','failed'),
                                       ('blocked','blocked'),('ready','stopped')])
def test_progress_only_accepts_exact_verified_command_receipt(state,kind):
    progress=VerifiedShiftTaskProgress();tx=Tx(rows=[{'kind':kind}])
    p=dict(project_id='p',task_id='t',lease_generation=1,
           next_state=state,evidence_digest='e'*64)
    assert progress.require_stage(tx,**p) is None
    tx.rows=[{'kind':'wrong'}]
    fail('conflict',lambda:progress.require_stage(tx,**p))


def test_verified_shift_hello_and_QA_review_are_distinct_authority_sources():
    progress=VerifiedShiftTaskProgress();tx=Tx()
    req=dict(project_id='p',task_id='t',lease_generation=1,evidence_digest='a'*64)
    fail('conflict',lambda:progress.require_stage(tx,next_state='running',**req))
    tx.one_result={'hello_digest':'b'*64}
    fail('conflict',lambda:progress.require_stage(tx,next_state='running',**req))
    tx.one_result={'hello_digest':'a'*64}
    assert progress.require_stage(tx,next_state='running',**req) is None
    tx.one_result=None
    fail('conflict',lambda:progress.require_stage(tx,next_state='review',**req))
    tx.one_result={'ok':1}
    assert progress.require_stage(tx,next_state='review',**req) is None
    fail('conflict',lambda:progress.require_stage(tx,next_state='unsupported',**req))


@pytest.mark.parametrize('bad', [None,[],{'assignTask'},frozenset({''})])
def test_owner_operation_policy_is_immutable_and_explicit(bad):
    with pytest.raises(TypeError):CurrentProductionAuthorizationBuilder(bad)