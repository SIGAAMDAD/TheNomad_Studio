"""Real SQLite-backed WG-J tests, with injected/faulting remote provider ports."""
from __future__ import annotations
from _studio_v1_fixture import sql_for

import hashlib
import os
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import dataclass

from nomad_studio.contracts.v1 import CommandEnvelope, QueryEnvelope, VerifiedIdentity, Outcome
from nomad_studio.publication.api import build_component
from nomad_studio.publication.model import ProviderRateLimited, ProviderForbidden, ProviderConflict

BASE = 'a' * 40
NEXT = 'b' * 40
DIGEST_BYTES = b'GitHub immutable release manifest payload'


class Reader:
    def __init__(self, conn): self.conn=conn
    def fetch_one(self,sql,params=()):
        r=self.conn.execute(sql,params).fetchone()
        return dict(r) if r else None
    def fetch_all(self,sql,params=()): return [dict(x) for x in self.conn.execute(sql,params).fetchall()]


class Tx(Reader):
    def execute(self,sql,params=()):
        c=self.conn.execute(sql,params)
        return c.rowcount
    def append_event(self,event):
        self.conn.execute('INSERT INTO test_events(event_id,kind,intent_id) VALUES(?,?,?)',
                          (event.event_id,event.kind,event.causation_id))
        return event.event_id


class SqlStore:
    def __init__(self, filepath):
        self.conn=sqlite3.connect(filepath,check_same_thread=False,timeout=10,isolation_level=None)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.lock=threading.RLock()
        self.conn.executescript(sql_for('publication'))
        self.conn.execute('CREATE TABLE IF NOT EXISTS test_events(event_id TEXT PRIMARY KEY,kind TEXT,intent_id TEXT)')
    def transact(self, callback, *, idempotency_key=None):
        with self.lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                result=callback(Tx(self.conn))
                self.conn.execute('COMMIT')
                return result
            except BaseException:
                self.conn.execute('ROLLBACK')
                raise
    def read(self,callback):
        with self.lock: return callback(Reader(self.conn))
    def close(self): self.conn.close()


class Guard:
    def __init__(self): self.allowed=True; self.calls=[]
    name = 'trust'
    def query(self,request,identity):
        assert request.version == 'NOMAD/QUERY/1'
        assert request.domain == 'trust' and request.operation == 'trust.authorize'
        self.calls.append((request.payload['operation'],dict(request.payload['bindings'])))
        if self.allowed and identity.current_authority_epoch==1:
            return Outcome('ACCEPTED','AUTHORIZED')
        return Outcome('DENIED','REVOKED_GRANT')
    def execute(self,command,identity):
        return Outcome('DENIED','NOT_SUPPORTED')


class GitHubPort:
    def __init__(self):
        self.refs={'refs/heads/feature':BASE,'refs/heads/base':BASE}
        self.commits={NEXT:(DIGEST_BYTES,'effect-1')}
        self.cas_calls=0
        self.pr_calls=0
        self.timeout_after_cas=False
        self.timeout_after_pr=False
        self.read_error=False
        self.prs=[]
        self.error=None
        self.pr_lag=False
    def read_ref(self,repo,ref):
        if self.read_error: raise TimeoutError('read timeout')
        return self.refs.get(ref)
    def compare_and_swap_ref(self,repo,ref,expected,new,force=False):
        if force: raise AssertionError('force is prohibited')
        self.cas_calls+=1
        if self.error: raise self.error
        if self.refs.get(ref)!=expected: raise ProviderConflict('atomic CAS failed')
        self.refs[ref]=new
        if self.timeout_after_cas: raise TimeoutError('remote succeeded before timeout')
        return {'operation_id':'untrusted-receipt'}
    def read_commit_artifact(self,repo,sha,path):
        return self.commits[sha][0]
    def read_commit_effect_key(self,repo,sha):
        return self.commits[sha][1]
    def find_pull_requests(self,repo,head,base):
        if self.read_error: raise TimeoutError('PR read timed out')
        return [] if self.pr_lag else list(self.prs)
    def create_pull_request(self,repo,head,base,title,body):
        self.pr_calls+=1
        self.prs.append({'id':self.pr_calls,'head_revision':self.refs[head],'body':body})
        if self.timeout_after_pr: raise TimeoutError('created but response lost')
        return {'id':self.pr_calls}


class Artifacts:
    def __init__(self,data): self.data=data
    def read(self,ref): return self.data


class DrivePort:
    atomic_revision_cas=True
    def __init__(self):
        self.revision='v1'; self.parents=['folder-1']; self.data=b'old'
        self.effect_key='old-effect'; self.calls=0; self.timeout_after_effect=False
        self.export_error=False; self.fake_receipt=False; self.error=None
    def read_file(self,file_id):
        return {'revision':self.revision,'parents':list(self.parents),'effect_key':self.effect_key}
    def replace_if_revision(self,file_id,expected,data,parent,mime,effect_key):
        self.calls+=1
        if self.error: raise self.error
        if self.revision != expected: raise ProviderConflict('CAS revision changed')
        self.revision='v2'; self.data=data; self.effect_key=effect_key
        if self.timeout_after_effect: raise TimeoutError('Drive effect committed, ack lost')
        return {'success':True,'verified':True}  # deliberately untrusted
    def export_file(self,file_id,mime):
        if self.export_error: raise TimeoutError('export missing')
        return b'forged data' if self.fake_receipt else self.data


def identity(scopes=None):
    return VerifiedIdentity('principal-A','cred-A','trusted-gateway','mtls',
            frozenset(scopes or ('publication.prepare','publication.dispatch',
              'publication.readback','publication.reconcile','publication.query',
              'publication.conflict')),10**18,1)


def payload(provider='github',action='update_ref',data=DIGEST_BYTES):
    b={'job_id':'job-1','assignment_epoch':2,'source_sha':BASE,
       'source_path':'src/game/file.py','lease_id':'lease-1','lease_epoch':3,
       'qa_receipt_id':'qa-100','qa_subject_sha':BASE,'approval_ref':'grant-1'}
    if action=='update_ref':
        target={'repository':'owner/repo','ref':'refs/heads/feature',
                'proposed_revision':NEXT,'manifest_path':'release/manifest.json'}
    elif action=='create_pr':
        target={'repository':'owner/repo','head_ref':'refs/heads/feature',
                'base_ref':'refs/heads/base','head_revision':NEXT,
                'manifest_path':'release/manifest.json','title':'Change','body':'Approved change'}
    else:
        target={'file_id':'file-1','parent_id':'folder-1','mime_type':'application/pdf'}
    return {'provider':provider,'action':action,'effect_key':'effect-1',
            'artifact_ref':'immutable-artifact-1','artifact_sha256':hashlib.sha256(data).hexdigest(),
            'artifact_size':len(data),'expected_revision':BASE if provider=='github' else 'v1',
            'target':target,'bindings':b}


def cmd(op,p,idempotency=None):
    return CommandEnvelope('NOMAD/CMD/1','request-1','publication','publication.'+op,
                           idempotency or ('idem-'+op), job_id='job-1',
                           assignment_epoch=2,source_sha=BASE,payload=p)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=os.path.join(self.temp.name,'state.sqlite3')
        self.store=SqlStore(self.db)
        self.gh=GitHubPort(); self.drive=DrivePort(); self.guard=Guard()
        self.artifacts=Artifacts(b'new export bytes')
        self.now=100_000_000
        self.deps={'store':self.store,'authorization':self.guard,'github_transport':self.gh,
          'drive_transport':self.drive,'artifact_reader':self.artifacts,
          'clock_us':lambda:self.now,'quotas':{'github':{'max_per_window':20,'window_us':60_000_000},
           'drive':{'max_per_window':20,'window_us':60_000_000}}, 'backoff_us':1000}
        self.component=build_component(self.deps)
        self.user=identity()
    def tearDown(self): self.store.close();self.temp.cleanup()
    def prepare(self,p=None,idempotency='prepare-1'):
        result=self.component.execute(cmd('prepare',p or payload(),idempotency),self.user)
        self.assertEqual(result.code,'PREPARED',result)
        return result.payload['intent_id']
    def act(self,action,intent):
        return self.component.execute(cmd(action,{'intent_id':intent},'action-'+action),self.user)
    def state(self,intent):
        return self.store.read(lambda r:r.fetch_one('SELECT * FROM publication_intents WHERE intent_id=?',(intent,)))

    def test_crash_after_reserve_before_provider_effect_recovers(self):
        intent=self.prepare()
        self.assertEqual(self.gh.cas_calls,0)
        recovered=build_component(self.deps)
        out=recovered.execute(cmd('dispatch',{'intent_id':intent}),self.user)
        self.assertEqual(out.code,'VERIFIED')
        self.assertEqual(self.gh.cas_calls,1)
        self.assertEqual(self.state(intent)['state'],'VERIFIED')
        self.assertEqual(recovered.execute(cmd('dispatch',{'intent_id':intent}),self.user).code,'VERIFIED')
        self.assertEqual(self.gh.cas_calls,1)

    def test_timeout_after_github_success_reconciled_without_repeat(self):
        intent=self.prepare()
        self.gh.timeout_after_cas=True
        result=self.act('dispatch',intent)
        self.assertEqual(result.state,'UNKNOWN')
        self.assertEqual(self.state(intent)['state'],'PENDING_UNKNOWN')
        recovered=self.act('readback',intent)
        self.assertEqual(recovered.code,'VERIFIED')
        self.assertEqual(self.act('dispatch',intent).code,'VERIFIED')
        self.assertEqual(self.gh.cas_calls,1)

    def test_injection_crash_after_effect_before_durable_finish(self):
        intent=self.prepare()
        orig=self.component._finish_dispatch
        def crash(*args,**kwargs): raise KeyboardInterrupt('injected_process_crash')
        self.component._finish_dispatch=crash
        with self.assertRaises(KeyboardInterrupt): self.act('dispatch',intent)
        self.assertEqual(self.state(intent)['state'],'DISPATCHING')
        self.assertEqual(self.gh.cas_calls,1)
        self.assertEqual(build_component(self.deps).execute(cmd('reconcile',{'intent_id':intent}),self.user).code,'VERIFIED')
        self.assertEqual(self.gh.cas_calls,1)

    def test_expected_oid_conflict_dead_letters(self):
        intent=self.prepare();self.gh.refs['refs/heads/feature']='c'*40
        o=self.act('dispatch',intent)
        self.assertEqual(o.state,'CONFLICT')
        self.assertEqual(self.state(intent)['state'],'CONFLICT')
        self.assertEqual(self.gh.cas_calls,0)
        dead=self.store.read(lambda r:r.fetch_all('SELECT * FROM publication_dead_letters'))
        self.assertEqual(len(dead),1)

    def test_forged_provider_receipt_cannot_mark_verified(self):
        intent=self.prepare();self.gh.commits[NEXT]=(b'wrong bytes','effect-1')
        self.assertEqual(self.act('dispatch',intent).state,'CONFLICT')
        self.assertNotEqual(self.state(intent)['state'],'VERIFIED')
        self.assertEqual(self.gh.cas_calls,0)

    def test_failed_readback_preserves_unknown(self):
        intent=self.prepare()
        old=self.gh.compare_and_swap_ref
        def flip(*args,**kw):
            value=old(*args,**kw);self.gh.read_error=True;return value
        self.gh.compare_and_swap_ref=flip
        o=self.act('dispatch',intent)
        self.assertEqual(o.state,'UNKNOWN')
        self.assertEqual(self.state(intent)['state'],'PENDING_UNKNOWN')
        self.gh.read_error=False
        self.assertEqual(self.act('readback',intent).code,'VERIFIED')

    def test_github_429_backoff_persistent_quota_and_403(self):
        intent=self.prepare();self.gh.error=ProviderRateLimited(30_000)
        o=self.act('dispatch',intent)
        self.assertEqual(o.code,'BACKPRESSURE')
        self.assertEqual(self.state(intent)['state'],'PREPARED')
        self.assertEqual(self.act('dispatch',intent).code,'BACKPRESSURE')
        self.now+=31_000
        self.gh.error=ProviderForbidden('403 Forbidden')
        self.assertEqual(self.act('dispatch',intent).code,'NOT_SUPPORTED')
        self.assertEqual(self.state(intent)['state'],'BLOCKED')

    def test_quota_limits_per_provider(self):
        self.deps['quotas']={'github':{'max_per_window':1,'window_us':60_000_000},
                             'drive':{'max_per_window':1,'window_us':60_000_000}}
        self.component=build_component(self.deps)
        first=self.prepare()
        self.gh.error=ProviderRateLimited()
        self.assertEqual(self.act('dispatch',first).code,'BACKPRESSURE')
        p2=payload();p2['effect_key']='effect-2';p2['target']['proposed_revision']='c'*40
        second=self.prepare(p2,'prepare-2')
        self.assertEqual(self.act('dispatch',second).code,'BACKPRESSURE')
        self.assertEqual(self.state(second)['attempt_count'],0)

    def test_drive_verified_export_bytes_parent_and_revision(self):
        data=self.artifacts.data
        intent=self.prepare(payload('drive','replace_file',data))
        self.assertEqual(self.act('dispatch',intent).code,'VERIFIED')
        self.assertEqual(self.drive.calls,1)
        self.assertEqual(self.drive.revision,'v2')
        self.assertEqual(self.act('dispatch',intent).code,'VERIFIED')
        self.assertEqual(self.drive.calls,1)

    def test_drive_timeout_after_success_readback_content_ambiguity(self):
        intent=self.prepare(payload('drive','replace_file',self.artifacts.data))
        self.drive.timeout_after_effect=True;self.drive.export_error=True
        self.assertEqual(self.act('dispatch',intent).state,'UNKNOWN')
        self.assertEqual(self.act('readback',intent).state,'UNKNOWN')
        self.drive.export_error=False
        self.assertEqual(self.act('reconcile',intent).code,'VERIFIED')
        self.assertEqual(self.drive.calls,1)

    def test_drive_expected_revision_and_parent_conflicts(self):
        intent=self.prepare(payload('drive','replace_file',self.artifacts.data))
        self.drive.revision='v-other'
        self.assertEqual(self.act('dispatch',intent).state,'CONFLICT')
        self.assertEqual(self.drive.calls,0)
        self.assertEqual(self.state(intent)['state'],'CONFLICT')

    def test_drive_forged_receipt_not_verification(self):
        intent=self.prepare(payload('drive','replace_file',self.artifacts.data))
        self.drive.fake_receipt=True
        self.assertEqual(self.act('dispatch',intent).state,'CONFLICT')
        self.assertEqual(self.state(intent)['state'],'CONFLICT')

    def test_drive_transport_without_atomic_compare_blocks_write(self):
        self.drive.atomic_revision_cas=False
        intent=self.prepare(payload('drive','replace_file',self.artifacts.data))
        self.assertEqual(self.act('dispatch',intent).code,'NOT_SUPPORTED')
        self.assertEqual(self.drive.calls,0)

    def test_pr_timeout_must_not_resend_while_absent_index(self):
        self.gh.refs['refs/heads/feature']=NEXT
        pr_payload=payload('github','create_pr')
        pr_payload['expected_revision']=NEXT
        intent=self.prepare(pr_payload)
        self.gh.timeout_after_pr=True;self.gh.pr_lag=True
        self.assertEqual(self.act('dispatch',intent).state,'UNKNOWN')
        self.assertEqual(self.act('readback',intent).state,'UNKNOWN')
        self.assertEqual(self.act('dispatch',intent).state,'UNKNOWN')
        self.assertEqual(self.gh.pr_calls,1)
        self.gh.pr_lag=False
        self.assertEqual(self.act('reconcile',intent).code,'VERIFIED')

    def test_idempotency_and_effect_key_conflicts(self):
        i=self.prepare()
        r=self.component.execute(cmd('prepare',payload(),'prepare-1'),self.user)
        self.assertEqual(r.payload['intent_id'],i)
        changed=payload();changed['artifact_sha256']='0'*64
        self.assertEqual(self.component.execute(cmd('prepare',changed,'prepare-1'),self.user).code,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.component.execute(cmd('prepare',payload(),'prepare-2'),self.user).code,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.store.read(lambda r:len(r.fetch_all('SELECT * FROM publication_intents'))),1)

    def test_closed_fields_stale_assignments_revoked_grants(self):
        p=payload();p['password']='DO_NOT_SAVE'
        self.assertEqual(self.component.execute(cmd('prepare',p),self.user).code,'INVALID_SCHEMA')
        p=payload();p['bindings']['qa_subject_sha']='d'*40
        self.assertEqual(self.component.execute(cmd('prepare',p),self.user).code,'INVALID_SCHEMA')
        p=payload();p['bindings']['source_path']='src/../escape'
        self.assertEqual(self.component.execute(cmd('prepare',p),self.user).code,'INVALID_SCHEMA')
        self.guard.allowed=False
        self.assertEqual(self.component.execute(cmd('prepare',payload()),self.user).code,'REVOKED_GRANT')

    def test_readback_query_scope_and_status(self):
        i=self.prepare()
        q=QueryEnvelope('NOMAD/QUERY/1','q','publication','publication.status',payload={'intent_id':i})
        o=self.component.query(q,self.user)
        self.assertEqual(o.code,'OK')
        self.assertEqual(o.payload['items'][0]['state'],'PREPARED')
        outsider=identity(); outsider=VerifiedIdentity('other','cred','issuer','mtls',outsider.scopes,10**18,1)
        self.assertEqual(self.component.query(q,outsider).payload['items'],[])
        self.assertEqual(self.act('readback',i).code,'ABSENT_SAFE')

    def test_missing_integration_ports_blocks_effect(self):
        comp=build_component({'store':self.store,'github_transport':self.gh,'clock_us':lambda:self.now})
        self.assertEqual(comp.execute(cmd('prepare',payload()),self.user).code,'NOT_CONFIGURED')
        self.assertEqual(self.gh.cas_calls,0)

    def test_not_reconcile_on_bogus_data_for_same_revision(self):
        i=self.prepare(payload('drive','replace_file',self.artifacts.data))
        self.drive.revision='v2';self.drive.effect_key='effect-1';self.drive.data=b'wrong'
        self.assertEqual(self.act('readback',i).state,'CONFLICT')

    def test_lost_remote_revision_during_drive_readback_remains_unknown(self):
        i=self.prepare(payload('drive','replace_file',self.artifacts.data))
        orig=self.drive.export_file
        def change_after_export(*args):
            result=orig(*args)
            self.drive.revision='v3'
            return result
        self.drive.export_file=change_after_export
        self.assertEqual(self.act('dispatch',i).state,'UNKNOWN')
        self.assertEqual(self.state(i)['state'],'PENDING_UNKNOWN')

    def test_source_binding_denied_at_dispatch_after_revoke(self):
        i=self.prepare()
        self.guard.allowed=False
        self.assertEqual(self.act('dispatch',i).code,'REVOKED_GRANT')
        self.assertEqual(self.gh.cas_calls,0)
        self.assertEqual(self.state(i)['state'],'PREPARED')

    def test_local_drive_digest_mismatch_denies_publish(self):
        i=self.prepare(payload('drive','replace_file',self.artifacts.data))
        self.artifacts.data=b'wrong source bytes'
        self.assertEqual(self.act('dispatch',i).state,'CONFLICT')
        self.assertEqual(self.drive.calls,0)

    def test_pending_scan_is_paginated_and_principal_scoped(self):
        ids=[]
        for n in range(3):
            p=payload();p['effect_key']=f'effect-{n}'
            ids.append(self.prepare(p,f'prepare-{n}'))
        q=lambda cursor=None: QueryEnvelope('NOMAD/QUERY/1','q','publication',
                                'publication.pending',cursor=cursor,limit=2,payload={})
        first=self.component.query(q(),self.user)
        self.assertEqual(first.code,'OK')
        self.assertEqual(len(first.payload['items']),2)
        second=self.component.query(q(first.payload['next_cursor']),self.user)
        self.assertEqual(len(second.payload['items']),1)
        outsider=VerifiedIdentity('other','c','i','mtls',self.user.scopes,10**18,1)
        self.assertEqual(self.component.query(q(),outsider).payload['items'],[])

    def test_transaction_event_failure_rolls_back_preparation(self):
        orig=self.component._event
        def inject(*a,**kw): raise RuntimeError('journal temporarily failed')
        self.component._event=inject
        self.assertEqual(self.component.execute(cmd('prepare',payload()),self.user).code,'PROVIDER_UNKNOWN')
        count=self.store.read(lambda r:len(r.fetch_all('SELECT * FROM publication_intents')))
        self.assertEqual(count,0)
        self.component._event=orig
        self.assertEqual(self.component.execute(cmd('prepare',payload()),self.user).code,'PREPARED')

    def test_pr_success_verified_after_immutable_readback(self):
        self.gh.refs['refs/heads/feature']=NEXT
        p=payload('github','create_pr');p['expected_revision']=NEXT
        i=self.prepare(p)
        self.assertEqual(self.act('dispatch',i).code,'VERIFIED')
        self.assertEqual(self.gh.pr_calls,1)
        self.assertIn('nomad-publication:',self.gh.prs[0]['body'])
        self.assertEqual(self.act('dispatch',i).code,'VERIFIED')
        self.assertEqual(self.gh.pr_calls,1)

    def test_prepared_id_cannot_be_stolen_by_other_worker(self):
        i=self.prepare()
        outsider=VerifiedIdentity('other','c','i','mtls',self.user.scopes,10**18,1)
        out=self.component.execute(cmd('dispatch',{'intent_id':i}),outsider)
        self.assertEqual(out.code,'INVALID_SCOPE')
        self.assertEqual(self.gh.cas_calls,0)

    def test_multiple_simultaneous_dispatches_never_double_publish(self):
        i=self.prepare()
        answers=[]
        def worker(): answers.append(self.act('dispatch',i))
        threads=[threading.Thread(target=worker) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(self.gh.cas_calls,1)
        self.assertTrue(any(x.code=='VERIFIED' for x in answers))
        self.assertEqual(self.state(i)['state'],'VERIFIED')


if __name__=='__main__': unittest.main()
