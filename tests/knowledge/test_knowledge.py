"""Isolated WG-S validation with a real SQLite SQL engine and frozen-port fakes.

No fake is represented as a real GitHub/Drive connector or production authority.
"""
import hashlib
import json
import sqlite3
import threading
import time
import unittest
from pathlib import Path

from nomad_studio.contracts.v1 import (CommandEnvelope, QueryEnvelope, Outcome,
                                      VerifiedIdentity)
from nomad_studio.knowledge.api import build_component, canonical, digest

MIGRATION=Path(__file__).resolve().parents[2] / 'src/nomad_studio/state/schema_v1.sql'


def git_blob(text):
    raw=text.encode('utf-8')
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


class FakeStore:
    """In-memory test-only StateStore. Real SQL/constraints, no Studio writer."""
    def __init__(self):
        self.db=sqlite3.connect(':memory:', check_same_thread=False, isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
        self.db.executescript(MIGRATION.read_text())
        self.events=[]
        self.fail_next=False

    def fetch_one(self,sql,params=()):
        row=self.db.execute(sql,params).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self,sql,params=()):
        return [dict(x) for x in self.db.execute(sql,params).fetchall()]

    def execute(self,sql,params=()):
        return self.db.execute(sql,params).rowcount

    def append_event(self,event):
        self.events.append(event)
        return event.event_id

    def read(self,fn):
        with self.lock:
            return fn(self)

    def transact(self,fn,*,idempotency_key=None):
        with self.lock:
            snapshot=len(self.events)
            self.db.execute('BEGIN IMMEDIATE')
            try:
                result=fn(self)
                if self.fail_next:
                    self.fail_next=False
                    raise OSError('test injected crash before commit')
                self.db.execute('COMMIT')
                return result
            except BaseException:
                self.db.execute('ROLLBACK')
                del self.events[snapshot:]
                raise

    def close(self):
        self.db.close()


class FakeReadback:
    """Frozen Component fake returns proof only from its independent source map."""
    name='readback-fake'
    def __init__(self):
        self.records={}
        self.fail=None
        self.calls=0
        self.revision_override={}
        self.classification_override={}
        self.bad_hash=False
        self.bad_verified=False
        self.bad_echo=False

    def add(self,source,text,classification='INTERNAL',folder_ref='game/design'):
        self.records[canonical(source)]=(text,classification,folder_ref)

    def query(self,query,identity):
        self.calls+=1
        if self.fail=='timeout':
            raise TimeoutError('injected network outage')
        if self.fail=='unknown':
            return Outcome('UNKNOWN','PROVIDER_UNKNOWN')
        if self.fail=='notrun':
            return Outcome('NOT_RUN','NOT_CONFIGURED')
        self.last_identity=identity
        s=query.payload['source']
        info=self.records.get(canonical(s))
        if info is None:
            return Outcome('UNKNOWN','PROVIDER_UNKNOWN')
        content,classification,folder=info
        revision=s['revision_id'] if s['type']=='drive' else s['commit_sha']+':'+s['blob_sha']
        revision=self.revision_override.get(canonical(s),revision)
        classification=self.classification_override.get(canonical(s),classification)
        digest_=hashlib.sha256(content.encode()).hexdigest()
        proof={'source':s if not self.bad_echo else {'fake':'wrong'},
               'current_revision':revision,'content':content,
               'content_sha256':'0'*64 if self.bad_hash else digest_,
               'classification':classification,'readback_id':'test-readback-'+digest_,
               'folder_ref':folder,'verified':not self.bad_verified}
        return Outcome('ACCEPTED','VERIFIED',payload=proof)

    def execute(self,command,identity):
        raise AssertionError('knowledge must never mutate provider')


NOW=2**62

def ident(*scopes,owner=False):
    s={'knowledge:provider:readback'} if 'provider' in scopes else set(scopes)
    if owner:
        s |= {'knowledge:accept_canon','owner:canon'}
    return VerifiedIdentity('owner-1' if owner else 'worker-1', 'trusted-cred', 'trusted-gateway',
                            'gateway-test',frozenset(s), NOW,1)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.store=FakeStore()
        self.git=FakeReadback()
        self.drive=FakeReadback()
        self.component=build_component({'store':self.store,'source':self.git,'artifacts':self.drive,
                                        'service_identity':ident('provider'),
                                        'owner_principals':['owner-1']})
        self.worker=ident('knowledge:index','knowledge:read','knowledge:invalidate',
                          'knowledge:role:ai-programmer','knowledge:department:engineering',
                          'knowledge:job:job-1','knowledge:history')
        self.owner=ident('knowledge:read',owner=True)
        self.drive_ref={'type':'drive','file_id':'file-xyz','revision_id':'rev-1'}
        self.drive.add(self.drive_ref,'Raio moves across the safe sands; lore canon seed.',folder_ref='game/narrative')
        self.git_text='Movement rules: no combat inventory, high speed and low profile.'
        self.git_ref={'type':'git','repo':'Nomad/Game','path':'docs/gameplay/movement.md',
                      'ref':'main','commit_sha':'a'*40,'blob_sha':git_blob(self.git_text)}
        self.git.add(self.git_ref,self.git_text,folder_ref='game/gameplay')
        self.n=0

    def tearDown(self):
        self.store.close()

    def cmd(self,operation,payload,identity=None,key=None):
        self.n+=1
        return self.component.execute(CommandEnvelope('NOMAD/CMD/1','c'+str(self.n),
                    'knowledge','knowledge.'+operation,key or 'k'+str(self.n),
                    job_id='job-1',payload=payload),identity or self.worker)

    def qry(self,operation,payload,identity=None,job_id='job-1'):
        self.n+=1
        return self.component.query(QueryEnvelope('NOMAD/QUERY/1','q'+str(self.n),
                    'knowledge','knowledge.'+operation,job_id=job_id,payload=payload),identity or self.worker)

    def add_source(self,kind='drive',subject='raio',domain='narrative',roles=None,
                   departments=None,jobs=None,read_scopes=None):
        if kind=='drive':
            source=self.drive_ref
            folder='game/narrative'
        else:
            source=self.git_ref
            folder='game/gameplay'
        return self.cmd('index',{'source':source,'domain':domain,'subject':subject,
                 'folder_ref':folder,'roles':roles or [],'departments':departments or [],
                 'jobs':jobs or [],'read_scopes':read_scopes or []})

    def accept(self,record_id,subject='raio',identity=None):
        return self.cmd('accept_canon',{'subject':subject,'record_id':record_id,
                                        'approval_reference':'owner-request-123'},identity or self.owner)

    def context(self,**changes):
        p={'role':'ai-programmer','department':'engineering',
           'job_id':'job-1','domains':['narrative','gameplay'],'max_bytes':4096,
           'max_tokens':4096}
        p.update(changes)
        return self.qry('context',p)

    def test_owner_gate_and_canon_history(self):
        r=self.add_source()
        self.assertEqual(r.state,'ACCEPTED')
        self.assertEqual(self.context().payload['entries'],[])
        forged=self.accept(r.payload['record_id'],identity=self.worker)
        self.assertEqual((forged.state,forged.code),('DENIED','INVALID_SCOPE'))
        limited_owner=ident('knowledge:accept_canon','owner:canon')
        self.assertEqual(self.accept(r.payload['record_id'],identity=limited_owner).code,
                         'OWNER_APPROVAL_REQUIRED')
        accepted=self.accept(r.payload['record_id'])
        self.assertEqual(accepted.code,'CANON_ACCEPTED')
        self.assertEqual(accepted.payload['generation'],1)
        self.assertEqual(self.context().payload['entries'][0]['subject'],'raio')
        self.assertEqual(self.accept(r.payload['record_id']).code,'CANON_UNCHANGED')
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_canon_history')),1)

    def test_change_drive_invalidates_and_blocks_retrieval(self):
        r=self.add_source()
        self.accept(r.payload['record_id'])
        old=self.context()
        self.assertEqual(old.code,'CONTEXT_READY')
        self.drive.revision_override[canonical(self.drive_ref)]='rev-2'
        stale=self.context()
        self.assertEqual((stale.state,stale.code),('CONFLICT','REBASE_REQUIRED'))
        self.assertEqual(self.store.fetch_one('SELECT state FROM knowledge_exports WHERE digest=?',
                     (old.payload['digest'],))['state'],'STALE')
        self.assertEqual(self.store.fetch_one('SELECT latest_record_id FROM knowledge_heads')['latest_record_id'],None)
        self.assertEqual(self.accept(r.payload['record_id']).code,'REBASE_REQUIRED')

    def test_change_git_invalidates_and_mark_historical(self):
        r=self.add_source(kind='git',subject='move',domain='gameplay')
        self.accept(r.payload['record_id'],subject='move')
        old=self.context()
        self.git.revision_override[canonical(self.git_ref)]='b'*40+':'+self.git_ref['blob_sha']
        self.assertEqual(self.cmd('invalidate',{'source':self.git_ref}).code,'INVALIDATED')
        self.assertEqual(self.store.fetch_one('SELECT state FROM knowledge_exports WHERE digest=?',
                     (old.payload['digest'],))['state'],'STALE')
        rr=self.qry('resolve',{'subject':'move','include_history':True})
        self.assertEqual(rr.payload['records'][0]['status'],'STALE')
        self.assertEqual(self.context().code,'REBASE_REQUIRED')

    def test_source_scope_and_role_job_authorization(self):
        r=self.add_source(roles=['ai-programmer'],departments=['engineering'],jobs=['job-1'],
                          read_scopes=['knowledge:restricted'])
        self.accept(r.payload['record_id'])
        self.assertEqual(self.context().payload['entries'],[])
        self.worker=ident('knowledge:index','knowledge:read','knowledge:invalidate',
                          'knowledge:role:ai-programmer','knowledge:department:engineering',
                          'knowledge:job:job-1','knowledge:restricted','knowledge:history')
        good=self.context()
        self.assertEqual(good.code,'CONTEXT_READY')
        self.assertEqual(len(good.payload['entries']),1)
        self.assertEqual(good.payload['entries'][0]['citation']['source'],self.drive_ref)
        self.assertEqual(self.context(role='narrative-director').code,'INVALID_SCOPE')
        self.assertEqual(self.context(job_id='job-2').code,'INVALID_SCOPE')
        self.assertEqual(self.qry('context',dict(good.payload),job_id='wrong').code,'INVALID_SCHEMA')

    def test_oversight_rejected_and_not_written(self):
        self.drive.classification_override[canonical(self.drive_ref)]='OVERSIGHT_CONFIDENTIAL'
        result=self.add_source()
        self.assertEqual((result.state,result.code),('DENIED','INVALID_SCOPE'))
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),0)
        self.drive.classification_override.clear()
        result=self.cmd('index',{'source':self.drive_ref,'domain':'narrative','subject':'raio',
                   'folder_ref':'Oversight/private','roles':[],'departments':[],
                   'jobs':[],'read_scopes':[]})
        self.assertEqual(result.code,'INVALID_SCOPE')

    def test_determinism_reindex_and_digest(self):
        a=self.add_source(); b=self.add_source()
        self.assertEqual(a.payload['record_id'],b.payload['record_id'])
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),1)
        self.accept(a.payload['record_id'])
        x=self.context(); y=self.context()
        self.assertEqual(canonical(x.payload),canonical(y.payload))
        self.assertEqual(x.receipt_id,y.receipt_id)
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_exports')),1)
        self.assertLessEqual(x.payload['byte_count'],4096)
        self.assertLessEqual(x.payload['token_upper_bound'],4096)
        self.assertEqual(len(canonical(x.payload).encode('ascii')),x.payload['byte_count'])

    def test_closed_schema_and_forged_content(self):
        payload={'source':self.drive_ref,'domain':'narrative','subject':'raio',
                 'folder_ref':'game/narrative','roles':[],'departments':[],
                 'jobs':[],'read_scopes':[],'content':'FAKE FACT'}
        self.assertEqual(self.cmd('index',payload).code,'INVALID_SCHEMA')
        self.assertEqual(self.qry('context',{'role':'ai-programmer','department':'engineering',
                 'domains':['narrative'],'max_bytes':1000,'max_tokens':1000,
                 'canonical_acceptance':True}).code,'INVALID_SCHEMA')
        self.assertEqual(self.cmd('index',{'source':dict(self.drive_ref,unknown='abc')}).code,'INVALID_SCHEMA')
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),0)

    def test_hash_proof_and_git_hash_rejected(self):
        self.drive.bad_hash=True
        self.assertEqual(self.add_source().code,'SOURCE_SHA_MISMATCH')
        self.drive.bad_hash=False
        self.drive.bad_echo=True
        self.assertEqual(self.add_source().code,'PROVIDER_UNKNOWN')
        self.git.add(self.git_ref,'tampered bytes',folder_ref='game/gameplay')
        self.assertEqual(self.add_source('git','move','gameplay').code,'SOURCE_SHA_MISMATCH')
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),0)

    def test_missing_provider_and_faults_and_no_partial_context(self):
        missing=build_component({'store':self.store,'service_identity':ident('provider')})
        cmd=CommandEnvelope('NOMAD/CMD/1','x','knowledge','knowledge.index','k-missing',
                            payload={'source':self.drive_ref,'domain':'narrative','subject':'raio',
                                    'folder_ref':'game/narrative','roles':[],'departments':[],
                                    'jobs':[],'read_scopes':[]})
        self.assertEqual((missing.execute(cmd,self.worker).state,missing.execute(cmd,self.worker).code),
                         ('NOT_RUN','NOT_CONFIGURED'))
        r=self.add_source()
        self.accept(r.payload['record_id'])
        self.drive.fail='timeout'
        self.assertEqual(self.context().code,'PROVIDER_UNKNOWN')
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_exports')),0)
        self.drive.fail=None
        self.assertEqual(self.context().code,'CONTEXT_READY')
        self.drive.fail='unknown'
        self.assertEqual(self.accept(r.payload['record_id']).code,'PROVIDER_UNKNOWN')

    def test_budget_truncation_and_no_provider_content_fabrication(self):
        self.drive.add(self.drive_ref,'A'*5000,folder_ref='game/narrative')
        r=self.add_source(); self.accept(r.payload['record_id'])
        ctx=self.context(max_bytes=1024,max_tokens=1024)
        self.assertEqual(ctx.code,'CONTEXT_READY')
        self.assertTrue(ctx.payload['truncated'])
        self.assertLessEqual(len(canonical(ctx.payload).encode()),1024)
        self.assertLessEqual(ctx.payload['byte_count'],1024)
        self.assertIn('citation',ctx.payload['entries'][0])
        self.assertEqual(self.context(max_bytes=511).code,'INVALID_SCHEMA')

    def test_atomic_idempotency_and_conflicting_replay(self):
        self.n+=1
        key='shared'
        payload={'source':self.drive_ref,'domain':'narrative','subject':'raio',
                 'folder_ref':'game/narrative','roles':[],'departments':[],
                 'jobs':[],'read_scopes':[]}
        one=self.cmd('index',payload,key=key)
        two=self.cmd('index',payload,key=key)
        self.assertEqual((one.payload,two.payload),(one.payload,one.payload))
        self.assertEqual(len(self.store.events),1)
        changed=dict(payload,subject='other')
        self.assertEqual(self.cmd('index',changed,key=key).code,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),1)

    def test_failed_transaction_rolls_back_event(self):
        self.store.fail_next=True
        fault=self.add_source()
        self.assertEqual((fault.state,fault.code),('BLOCKED','STORE_FAILURE'))
        self.assertEqual(len(self.store.events),0)
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),0)
        self.assertEqual(self.add_source().code,'INDEXED')

    def test_concurrent_same_source_indexing_deterministic(self):
        payload={'source':self.drive_ref,'domain':'narrative','subject':'raio',
                 'folder_ref':'game/narrative','roles':[],'departments':[],
                 'jobs':[],'read_scopes':[]}
        results=[]
        lock=threading.Lock()
        def thread_fn(i):
            command=CommandEnvelope('NOMAD/CMD/1','p'+str(i),'knowledge','knowledge.index',
                                    'parallel'+str(i),payload=payload)
            res=self.component.execute(command,self.worker)
            with lock:
                results.append(res)
        threads=[threading.Thread(target=thread_fn,args=(i,)) for i in range(30)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(results),30)
        self.assertEqual({r.code for r in results},{'INDEXED'})
        self.assertEqual(len({r.payload['record_id'] for r in results}),1)
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),1)

    def test_new_revision_retains_old_canon_as_historical_until_owner_accepts(self):
        a=self.add_source(); self.accept(a.payload['record_id'])
        self.drive_ref={'type':'drive','file_id':'file-xyz','revision_id':'rev-2'}
        self.drive.add(self.drive_ref,'Updated lore',folder_ref='game/narrative')
        b=self.add_source()
        self.assertNotEqual(a.payload['record_id'],b.payload['record_id'])
        self.assertEqual(self.context().code,'REBASE_REQUIRED')
        self.accept(b.payload['record_id'])
        self.assertEqual(self.context().payload['entries'][0]['citation']['source']['revision_id'],'rev-2')
        history=self.store.fetch_all('SELECT * FROM knowledge_canon_history ORDER BY generation')
        self.assertEqual([x['generation'] for x in history],[1,2])
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),2)

    def test_unapproved_reference_requires_explicit_scope_and_label(self):
        indexed=self.add_source()
        self.assertEqual(indexed.code,'INDEXED')
        self.assertEqual(self.context().payload['entries'],[])
        self.assertEqual(self.context(include_references=True).code,'INVALID_SCOPE')
        self.worker=ident('knowledge:read','knowledge:reference:read',
                          'knowledge:role:ai-programmer','knowledge:department:engineering',
                          'knowledge:job:job-1')
        entry=self.context(include_references=True).payload['entries'][0]
        self.assertEqual(entry['authority'],'REFERENCE_UNAPPROVED')
        self.assertIsNone(entry['canon_generation'])
        self.assertEqual(entry['citation']['source'],self.drive_ref)
        self.assertEqual(self.store.fetch_all('SELECT * FROM knowledge_canon'),[])

    def test_replay_survives_provider_outage_and_changed_replay_conflicts(self):
        p={'source':self.drive_ref,'domain':'narrative','subject':'raio',
           'folder_ref':'game/narrative','roles':[],'departments':[],
           'jobs':[],'read_scopes':[]}
        a=self.cmd('index',p,key='replay-key')
        self.drive.fail='timeout'
        b=self.cmd('index',p,key='replay-key')
        self.assertEqual(a,b)
        c=self.cmd('index',dict(p,subject='bad'),key='replay-key')
        self.assertEqual(c.code,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(len(self.store.events),1)

    def test_index_metadata_conflict_is_not_a_mutable_canon_edit(self):
        a=self.add_source()
        b=self.add_source(subject='altered')
        self.assertEqual(a.code,'INDEXED')
        self.assertEqual(b.code,'DOCUMENT_CONFLICT')
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),1)
        self.assertEqual(self.store.fetch_one('SELECT subject FROM knowledge_sources')['subject'],'raio')

    def test_invalid_git_path_rejected_before_external_readback(self):
        bad=dict(self.git_ref,path='../private/secrets.txt')
        p={'source':bad,'domain':'gameplay','subject':'move',
           'folder_ref':'game/gameplay','roles':[],'departments':[],
           'jobs':[],'read_scopes':[]}
        calls=self.git.calls
        self.assertEqual(self.cmd('index',p).code,'INVALID_PATH')
        self.assertEqual(self.git.calls,calls)

    def test_provider_unavailable_returns_explicit_not_run(self):
        self.drive.fail='notrun'
        self.assertEqual(self.add_source().state,'NOT_RUN')
        self.drive.fail=None
        comp=build_component({'store':self.store,'artifacts':self.drive})
        command=CommandEnvelope('NOMAD/CMD/1','q1','knowledge','knowledge.index',
               'miss-service',payload={'source':self.drive_ref,'domain':'narrative','subject':'raio',
                    'folder_ref':'game/narrative','roles':[],'departments':[],
                    'jobs':[],'read_scopes':[]})
        self.assertEqual(comp.execute(command,self.worker).state,'NOT_RUN')

    def test_reader_filters_denied_without_role_or_job_grants(self):
        r=self.add_source(roles=['ai-programmer'],departments=['engineering'],jobs=['job-1'])
        self.accept(r.payload['record_id'])
        no_filters=self.qry('resolve',{'subject':'raio','include_history':True})
        self.assertEqual(no_filters.payload['records'],[])
        with_filters=self.qry('resolve',{'subject':'raio','include_history':True,
                          'role':'ai-programmer','department':'engineering','job_id':'job-1'})
        self.assertEqual(len(with_filters.payload['records']),1)
        self.assertEqual(with_filters.payload['records'][0]['accepted_generation'],1)
        self.assertEqual(self.qry('resolve',{'subject':'raio','role':'made-up'}).code,'INVALID_SCOPE')

    def test_generation_fences_race_between_readback_and_persistence(self):
        a=self.add_source(); self.accept(a.payload['record_id'])
        old=self.store.fetch_one('SELECT generation FROM knowledge_epochs')['generation']
        original=self.drive.query
        invoked=[False]
        def racing(query,identity):
            result=original(query,identity)
            if not invoked[0]:
                invoked[0]=True
                self.store.transact(lambda tx: tx.execute(
                    'UPDATE knowledge_epochs SET generation=generation+1 WHERE id=1'))
            return result
        self.drive.query=racing
        ctx=self.context()
        self.assertEqual((ctx.state,ctx.code),('CONFLICT','REBASE_REQUIRED'))
        self.assertEqual(self.store.fetch_one('SELECT generation FROM knowledge_epochs')['generation'],old+1)
        self.assertEqual(self.store.fetch_all('SELECT * FROM knowledge_exports'),[])

    def test_empty_context_is_staled_by_new_owner_accepted_knowledge(self):
        before=self.context()
        self.assertEqual(before.payload['entries'],[])
        r=self.add_source(); self.accept(r.payload['record_id'])
        old=self.store.fetch_one('SELECT * FROM knowledge_exports WHERE digest=?',
                                 (before.receipt_id,))
        self.assertEqual(old['state'],'STALE')
        after=self.context()
        self.assertNotEqual(before.receipt_id,after.receipt_id)
        self.assertEqual(len(after.payload['entries']),1)

    def test_external_readback_never_runs_inside_write_transaction(self):
        original=self.drive.query
        def guard(query,identity):
            self.assertFalse(self.store.db.in_transaction)
            return original(query,identity)
        self.drive.query=guard
        a=self.add_source(); self.accept(a.payload['record_id'])
        self.assertEqual(self.context().code,'CONTEXT_READY')
        self.assertEqual(self.cmd('invalidate',{'source':self.drive_ref}).code,'UNCHANGED')

    def test_body_mutation_under_same_drive_revision_quarantines_canon(self):
        first=self.add_source()
        self.accept(first.payload['record_id'])
        bundle=self.context()
        self.drive.add(self.drive_ref,'Mutated bytes with an unchanged revision',
                       folder_ref='game/narrative')
        suspicious=self.add_source()
        self.assertEqual((suspicious.state,suspicious.code),
                         ('CONFLICT','SOURCE_SHA_MISMATCH'))
        self.assertIsNone(self.store.fetch_one(
            'SELECT latest_record_id FROM knowledge_heads')['latest_record_id'])
        old=self.store.fetch_one('SELECT state FROM knowledge_exports WHERE digest=?',
                  (bundle.receipt_id,))
        self.assertEqual(old['state'],'STALE')
        self.assertEqual(self.context().code,'REBASE_REQUIRED')

    def test_topic_and_max_items_select_deterministically_without_unrelated_provider_calls(self):
        one=self.add_source(); self.accept(one.payload['record_id'])
        other=self.add_source('git','movement','gameplay'); self.accept(other.payload['record_id'],'movement')
        before_drive=self.drive.calls
        before_git=self.git.calls
        result=self.context(topic='movement',max_items=1)
        self.assertEqual(result.code,'CONTEXT_READY')
        self.assertEqual(len(result.payload['entries']),1)
        self.assertEqual(result.payload['entries'][0]['subject'],'movement')
        self.assertGreater(self.git.calls,before_git)
        self.assertEqual(self.drive.calls,before_drive)
        all_=self.context(max_items=1)
        self.assertTrue(all_.payload['truncated'])
        self.assertEqual(len(all_.payload['entries']),1)

    def test_expired_and_unverified_identity_cannot_index(self):
        expired=VerifiedIdentity('worker-1','cred','gateway','gateway-test',
             frozenset({'knowledge:index'}),1,1)
        p={'source':self.drive_ref,'domain':'narrative','subject':'raio',
           'folder_ref':'game/narrative','roles':[],'departments':[],
           'jobs':[],'read_scopes':[]}
        self.assertEqual(self.cmd('index',p,identity=expired).code,'IDENTITY_UNVERIFIED')
        self.assertEqual(self.cmd('index',p,identity='worker-1').code,'IDENTITY_UNVERIFIED')
        self.assertEqual(len(self.store.fetch_all('SELECT * FROM knowledge_sources')),0)

    def test_store_not_configured_is_not_run(self):
        comp=build_component({})
        self.assertEqual(comp.query(QueryEnvelope('NOMAD/QUERY/1','q','knowledge',
                            'knowledge.resolve',payload={'subject':'raio'}),self.worker).state,'NOT_RUN')


if __name__=='__main__':
    unittest.main()
