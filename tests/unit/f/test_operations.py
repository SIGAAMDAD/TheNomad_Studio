"""Offline SQLite *reference* unit tests for WG-F, NOT production APSW integration."""
from __future__ import annotations

from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
from studio.operations import Operations, Scope, DomainError, Schedule, ReportProfile, request_report, plans, SELECTS, WRITES

SCHEMA=Path(__file__).parent/'fixtures'/'initial_schema.sql'  # immutable plan-contract copy


class ReferenceTx:
    """Offline replacement for WG-C's named-plan engine; not production code."""
    def __init__(self, db, actor):
        self.db=db
        self.actor=actor
    def select_named(self,name,p):
        sql=SELECTS[name]
        return [dict(row) for row in self.db.execute(sql,p).fetchall()]
    def execute_named(self,name,p):
        return self.db.execute(WRITES[name],p).rowcount
    def record_event(self, *, event_type,subject_type,subject_id,payload_digest):
        project=self.actor.project_id
        seq=self.db.execute('SELECT COALESCE(MAX(event_seq),0)+1 FROM audit_events WHERE project_id=?',(project,)).fetchone()[0]
        self.db.execute('INSERT INTO audit_events(project_id,event_seq,event_type,subject_type,subject_id,payload_digest,actor_principal_id) VALUES(?,?,?,?,?,?,?)',
            (project,seq,event_type,subject_type,subject_id,payload_digest,self.actor.principal_id))


class ReferenceRunner:
    """Simulates C serialization and replay; in-memory response cache is TEST-ONLY."""
    def __init__(self,db):
        self.db=db
        self.result_cache={}
    def query(self,scope,fn):
        return fn(ReferenceTx(self.db,scope))
    def command(self,scope,*,owner,request_id,body_digest,fn):
        key=(scope.project_id,owner,request_id)
        existing=self.db.execute('SELECT payload_digest,status FROM idempotency WHERE project_id=? AND action_owner=? AND request_id=?',key).fetchone()
        if existing:
            if existing['payload_digest']!=body_digest:
                raise DomainError('conflict','divergent replay')
            return self.result_cache[key]
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO idempotency(project_id,action_owner,request_id,payload_digest,status) VALUES(?,?,?,?,?)',(*key,body_digest,'started'))
            result=fn(ReferenceTx(self.db,scope))
            dig=sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
            self.db.execute("UPDATE idempotency SET status='finished', result_digest=? WHERE project_id=? AND action_owner=? AND request_id=?",(dig,*key))
            self.db.execute('COMMIT')
            self.result_cache[key]=result
            return result
        except Exception:
            self.db.execute('ROLLBACK')
            raise


class KnownProviderEvidence:
    """TEST ONLY: explicit simulated remote readbacks for negative/positive tests."""
    def __init__(self): self.verified=True
    def verify_usage(self, **data):
        return self.verified and data['category']=='ci' and data['provider']=='github' \
           and data['evidence_digest']=='d'*64


class KnownClosure:
    def __init__(self): self.verified=True
    def verify_closure(self, **data): return self.verified and data['operation_id']=='build-1'


class InMemoryBlobs:
    def __init__(self):
        self.data={}
        self.writes=0
    def store_verified(self,project_id,body,*,purpose):
        digest=sha256(body).hexdigest()
        self.data[project_id,digest]=body
        self.writes+=1
        return digest
    def load_verified(self,project_id,digest):
        return self.data[project_id,digest]


class UnreliableRelay:
    def __init__(self): self.calls=[]
    def queue_report(self,**data):
        self.calls.append(data)
        raise TimeoutError('external publication ambiguous')


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:',isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.executescript(SCHEMA.read_text())
        for id in ('p1','p2'):
            self.db.execute('INSERT INTO projects(project_id,name,charter_digest,state) VALUES(?,?,?,?)',(id,id,'charter','active'))
        for id in ('alice','bob'):
            self.db.execute('INSERT INTO principals(principal_id,auth_issuer,auth_subject,kind) VALUES(?,?,?,?)',
                            (id,'test',id,'human'))
        self.alice=Scope('p1','alice','verified-j-scope',frozenset({'p1'}))
        self.bob=Scope('p2','bob','verified-j-scope',frozenset({'p2'}))
        self.runner=ReferenceRunner(self.db)
        self.usage_evidence=KnownProviderEvidence()
        self.closure=KnownClosure()
        self.ops=Operations(self.runner,usage_verifier=self.usage_evidence,
                            closure_verifier=self.closure)
    def tearDown(self): self.db.close()
    def assertCode(self,code,fn):
        with self.assertRaises(DomainError) as cm: fn()
        self.assertEqual(cm.exception.code,code)
    def configure(self,scope=None,*,name='ci',limit=100,rev=0,request='cfg'):
        return self.ops.configure_budget(scope or self.alice,budget_id=name,
                                         limit_amount=limit,expected_revision=rev,request_id=request)
    def reserve(self,*,rid='r1',value=70,request='res',scope=None):
        return self.ops.reserve(scope or self.alice,reservation_id=rid,budget_id='ci',
                                operation_id='build-1',value=value,request_id=request)
    def consume(self,*,rid='r1',usage='u1',ref='remote-run-1',value=60,evidence=None,scope=None,request='consume'):
        return self.ops.consume(scope or self.alice,reservation_id=rid,usage_id=usage,
            category='ci',provider='github',provider_ref=ref,actual_amount=value,
            evidence_digest=evidence or ('d'*64),request_id=request)
    def test_budget_cas_and_spend_floor(self):
        self.configure()
        self.reserve()
        self.assertCode('conflict',lambda:self.configure(limit=69,rev=1,request='cfg2'))
        self.assertCode('stale_revision',lambda:self.configure(limit=500,rev=0,request='cfg3'))
        self.configure(limit=90,rev=1,request='cfg4')
        self.assertEqual(self.ops.get_budgets(self.alice)['budgets'],[
            {'budget_id':'ci','limit_amount':90,'reserved_amount':70,'spent_amount':0}])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='budget.configured'").fetchone()[0],2)
    def test_idempotent_replay_and_divergence(self):
        self.assertEqual(self.configure(),self.configure())
        self.assertCode('conflict',lambda:self.configure(limit=99))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM budgets").fetchone()[0],1)
    def test_oversubscription_atomic(self):
        self.configure()
        self.reserve(value=99)
        self.assertCode('conflict',lambda:self.reserve(rid='r2',value=2,request='res2'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],1)
        self.assertEqual(self.ops.get_budgets(self.alice)['budgets'][0]['reserved_amount'],99)
    def test_consumption_partial_refund_provider_evidence(self):
        self.configure()
        self.reserve()
        r=self.consume(value=42)
        self.assertEqual(r['released_amount'],28)
        summary=self.ops.get_budgets(self.alice)['budgets'][0]
        self.assertEqual((summary['spent_amount'],summary['reserved_amount']),(42,0))
        rows=self.db.execute('SELECT provider_ref,amount,evidence_digest FROM usage_receipts').fetchall()
        self.assertEqual(json.loads(rows[0]['provider_ref']),['ci','github','remote-run-1'])
        self.assertEqual(rows[0]['amount'],42)
        self.assertEqual(rows[0]['evidence_digest'],'d'*64)
        self.assertCode('conflict',lambda:self.consume(ref='remote-run-2',usage='u2',request='consume2'))
    def test_no_usage_above_reserved_and_no_duplicate_provider_receipts(self):
        self.configure(limit=200)
        self.reserve()
        self.assertCode('conflict',lambda:self.consume(value=71))
        self.consume()
        self.reserve(rid='r2',value=55,request='res2')
        self.assertCode('conflict',lambda:self.consume(rid='r2',usage='u2',request='consume2',ref='remote-run-1'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM usage_receipts').fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT state FROM reservations WHERE reservation_id='r2'").fetchone()[0],'reserved')
    def test_release_no_double_refund(self):
        self.configure()
        self.reserve()
        self.ops.release(self.alice,reservation_id='r1',reason='canceled',request_id='release')
        self.assertCode('conflict',lambda:self.ops.release(self.alice,reservation_id='r1',reason='other',request_id='release2'))
        self.assertEqual(self.ops.get_budgets(self.alice)['budgets'][0]['reserved_amount'],0)
    def test_release_refuses_ambiguous_remote_operation(self):
        self.configure()
        self.reserve()
        self.closure.verified=False
        self.assertCode('unknown_effect',lambda:self.ops.release(self.alice,reservation_id='r1',
                                                  reason='canceled',request_id='proof-missing'))
        self.assertEqual(self.ops.get_budgets(self.alice)['budgets'][0]['reserved_amount'],70)
        self.assertEqual(self.ops.reservations_for_operation(self.alice,operation_id='build-1')[0]['state'],'reserved')
        self.closure.verified=True
        self.ops.release(self.alice,reservation_id='r1',reason='canceled',request_id='proof-ok')

    def test_department_report_requires_project_staffing(self):
        self.db.execute('INSERT INTO departments(department_id,name) VALUES (?,?)',('audio','Audio'))
        self.db.execute('INSERT INTO specialist_roles(role_id,role_revision,role_manifest_digest,department_id,state) VALUES(?,?,?,?,?)',
                        ('r_audio','v1','x'*64,'audio','ready'))
        self.db.execute('INSERT INTO cabals(project_id,cabal_id,name,charter_digest,state) VALUES(?,?,?,?,?)',
                        ('p1','cab1','First','x'*64,'active'))
        self.db.execute('INSERT INTO cabal_members(project_id,cabal_id,member_id,role_id,permission_digest) VALUES(?,?,?,?,?)',
                        ('p1','cab1','alice','r_audio','x'*64))
        blobs=InMemoryBlobs()
        digest=blobs.store_verified('p1',b'{"version":1,"scope":"department","department_id":"audio"}',purpose='profile')
        request_report(self.ops,self.alice,request_id='audio',report_profile_digest=digest,profiles=blobs,blobs=blobs)
        d=self.db.execute('SELECT report_digest FROM reports').fetchone()[0]
        content=json.loads(blobs.data['p1',d])
        self.assertEqual([(r['department_id'],r['members']) for r in content['departments']],[('audio',1)])
        # Another project cannot claim that same department is staffed.
        digest2=blobs.store_verified('p2',b'{"version":1,"scope":"department","department_id":"audio"}',purpose='profile')
        self.assertCode('missing',lambda:request_report(self.ops,self.bob,request_id='audio2',
            report_profile_digest=digest2,profiles=blobs,blobs=blobs))

    def test_scope_blocks_cross_project(self):
        self.configure()
        self.assertEqual(self.ops.get_budgets(self.bob)['budgets'],[])
        self.assertCode('forbidden',lambda:self.ops.get_budgets(Scope('p1','bob','proof',frozenset({'p2'}))))
        self.assertCode('missing',lambda:self.ops.release(self.bob,reservation_id='r1',reason='cancel',request_id='p2-rel'))
    def test_notification_ack_state_and_principal(self):
        self.ops.emit_notification(self.alice,notification_id='n1',kind='blocker',subject_id='task-23',request_id='n1')
        self.assertCode('stale_revision',lambda:self.ops.ack_notification(self.alice,notification_id='n1',expected_state='sent',request_id='bad'))
        res=self.ops.ack_notification(self.alice,notification_id='n1',expected_state='queued',request_id='ack')
        self.assertTrue(res['acknowledged'])
        self.assertEqual(self.db.execute("SELECT ack_principal_id FROM notifications").fetchone()[0],'alice')
        self.assertEqual(self.ops.ack_notification(self.alice,notification_id='n1',expected_state='queued',request_id='ack'),res)
        self.assertCode('stale_revision',lambda:self.ops.ack_notification(self.alice,notification_id='n1',expected_state='queued',request_id='ack2'))
        self.assertEqual(self.ops.list_notifications(self.bob)['notifications'],[])
    def test_notifications_do_not_forge_external_delivery(self):
        self.ops.emit_notification(self.alice,notification_id='n1',kind='decision',subject_id='d',request_id='n1')
        self.assertCode('unknown_effect',lambda:self.ops.mark_delivery(self.alice,kind='notification',record_id='n1',new_state='sent',expected_state='queued',request_id='mark'))
        self.assertEqual(self.db.execute('SELECT delivery_state FROM notifications').fetchone()[0],'queued')
    def test_report_generation_and_durable_digest(self):
        self.configure()
        self.reserve()
        self.consume()
        profile={'version':1,'scope':'studio','max_events':10}
        body=json.dumps(profile,sort_keys=True).encode()
        blobs=InMemoryBlobs()
        profile_digest=blobs.store_verified('p1',body,purpose='profile')
        at=datetime(2026,10,30,6,0,tzinfo=timezone.utc)
        r=request_report(self.ops,self.alice,request_id='report1',report_profile_digest=profile_digest,
                         profiles=blobs,blobs=blobs,observed_at=at)
        self.assertEqual(r['state'],'queued')
        dbrow=self.db.execute('SELECT report_digest,delivery_state FROM reports').fetchone()
        self.assertEqual(dbrow['delivery_state'],'queued')
        report=json.loads(blobs.data['p1',dbrow['report_digest']])
        self.assertEqual(report['budgets'][0]['spent_amount'],60)
        self.assertEqual(report['usage']['receipts'],1)
        self.assertTrue(report['recent_audit_events'][0]['event_seq']>0)
        self.assertEqual(report['profile_digest'],profile_digest)
        # An idempotent HTTP replay cannot render a new report with new clock.
        count=blobs.writes
        replay=request_report(self.ops,self.alice,request_id='report1',report_profile_digest=profile_digest,
                         profiles=blobs,blobs=blobs)
        self.assertEqual(replay,r)
        self.assertEqual(blobs.writes,count)
        self.assertCode('conflict',lambda:request_report(self.ops,self.alice,request_id='report1',
            report_profile_digest='a'*64,profiles=blobs,blobs=blobs))
        self.assertEqual(self.ops.list_reports(self.bob)['reports'],[])
    def test_report_delivery_requires_observed_effect_and_sql_trigger(self):
        blobs=InMemoryBlobs()
        profile_digest=blobs.store_verified('p1',b'{"version":1,"scope":"studio"}',purpose='profile')
        result=request_report(self.ops,self.alice,request_id='req',report_profile_digest=profile_digest,profiles=blobs,blobs=blobs)
        report_id=result['report_id']
        self.assertCode('unknown_effect',lambda:self.ops.mark_delivery(self.alice,kind='report',record_id=report_id,
            new_state='sent',expected_state='queued',request_id='sent'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE reports SET delivery_state='sent'")
        self.assertEqual(self.db.execute('SELECT delivery_state FROM reports').fetchone()[0],'queued')
        # A trusted external owner, not F, would record this after remote readback.
        self.db.execute("INSERT INTO effect_intents(project_id,effect_id,subject_type,subject_id,action,effect_digest,approval_required,state,provider_key,request_id) VALUES(?,?,?,?,?,?,0,'observed','github','ext-report')",
            ('p1','effect','report',report_id,'report.deliver','d'*64))
        self.ops.mark_delivery(self.alice,kind='report',record_id=report_id,new_state='sent',expected_state='queued',request_id='sent')
        self.assertEqual(self.db.execute('SELECT delivery_state FROM reports').fetchone()[0],'sent')
    def test_report_relay_timeout_does_not_fake_sent(self):
        blobs=InMemoryBlobs()
        profile_digest=blobs.store_verified('p1',b'{"version":1,"scope":"studio","destination_channel_id":"reports"}',purpose='profile')
        relay=UnreliableRelay()
        with self.assertRaises(TimeoutError):
            request_report(self.ops,self.alice,request_id='timeout-report',report_profile_digest=profile_digest,
                           profiles=blobs,blobs=blobs,relay=relay)
        self.assertEqual(self.db.execute("SELECT delivery_state FROM reports").fetchone()[0],'queued')
        self.assertEqual(len(relay.calls),1)
    def test_profile_tamper_rejected(self):
        blobs=InMemoryBlobs()
        self.assertCode('conflict',lambda:request_report(self.ops,self.alice,request_id='tamper',
           report_profile_digest='a'*64,profiles=type('Fake',(),{'load_verified':lambda *args:b'abc'})(),blobs=blobs))
    def test_bound_pagination(self):
        self.configure()
        for i in range(3): self.configure(name=f'c{i}',request=f'cfg-{i}')
        self.assertEqual(len(self.ops.get_budgets(self.alice,limit=2)['budgets']),2)
        self.assertCode('invalid',lambda:self.ops.get_budgets(self.alice,limit=10000))
        self.assertEqual(len(self.ops.get_budgets(self.alice,after='c1',limit=3)['budgets']),2)
    def test_no_model_inference_resource(self):
        self.configure()
        self.reserve()
        self.assertCode('invalid',lambda:self.ops.consume(self.alice,reservation_id='r1',usage_id='x',
                        category='llm_api',provider='x',provider_ref='x',actual_amount=1,
                        evidence_digest='a'*64,request_id='x'))
    def test_forged_provider_evidence_fails_closed(self):
        self.configure()
        self.reserve()
        self.usage_evidence.verified=False
        self.assertCode('unknown_effect',lambda:self.consume())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM usage_receipts').fetchone()[0],0)
        self.assertEqual(self.ops.get_budgets(self.alice)['budgets'][0]['reserved_amount'],70)
        self.assertCode('capability_unavailable',lambda:Operations(self.runner).consume(self.alice,
            reservation_id='r1',usage_id='u1',category='ci',provider='github',
            provider_ref='remote-run-1',actual_amount=60,evidence_digest='d'*64,request_id='other'))

    def test_report_replay_resubmits_ambiguous_delivery_with_original_bytes(self):
        blobs=InMemoryBlobs()
        digest=blobs.store_verified('p1',b'{"version":1,"scope":"studio","destination_channel_id":"reports"}',purpose='profile')
        relay=UnreliableRelay()
        with self.assertRaises(TimeoutError):
            request_report(self.ops,self.alice,request_id='same',report_profile_digest=digest,
                           profiles=blobs,blobs=blobs,relay=relay)
        stored=self.db.execute('SELECT report_digest FROM reports').fetchone()[0]
        # State and digest must survive retries; no extra content generated.
        count=blobs.writes
        with self.assertRaises(TimeoutError):
            request_report(self.ops,self.alice,request_id='same',report_profile_digest=digest,
                           profiles=blobs,blobs=blobs,relay=relay)
        self.assertEqual(blobs.writes,count)
        self.assertEqual([call['report_digest'] for call in relay.calls],[stored,stored])

    def test_scheduling_owner_selected_timezone(self):
        s=Schedule.from_json({'interval':'daily','timezone':'America/Chicago','hour':23,'minute':0})
        now=datetime(2026,10,31,5,0,tzinfo=timezone.utc)
        should,slot=s.due(now,None)
        self.assertTrue(should)
        self.assertEqual(slot,datetime(2026,10,31,4,0,tzinfo=timezone.utc))
        self.assertFalse(s.due(now,slot)[0])
        self.assertCode('invalid',lambda:Schedule.from_json({'interval':'daily','timezone':'Unknown/Nowhere',
                                                               'hour':12,'minute':30}))
    def test_bounded_report_under_hundreds_of_records(self):
        # Offline fixture checks output bounds without asserting runtime latency.
        for i in range(320):
            self.db.execute('INSERT INTO budgets(project_id,budget_id,resource_key,limit_amount) VALUES(?,?,?,?)',
                            ('p1',f'budget-{i:04}',f'res-{i}',200))
        for i in range(260):
            self.db.execute('INSERT INTO audit_events(project_id,event_seq,event_type,subject_type,subject_id,payload_digest,actor_principal_id) VALUES(?,?,?,?,?,?,?)',
                            ('p1',i+1,'fixture.event','task',f'task-{i}','c'*64,'alice'))
        blobs=InMemoryBlobs()
        digest=blobs.store_verified('p1',b'{"version":1,"scope":"studio","max_budgets":11,"max_events":13,"max_departments":5}',purpose='profile')
        request_report(self.ops,self.alice,request_id='bounded',report_profile_digest=digest,
                       profiles=blobs,blobs=blobs)
        report_digest=self.db.execute('SELECT report_digest FROM reports').fetchone()[0]
        doc=json.loads(blobs.data['p1',report_digest])
        self.assertEqual(len(doc['budgets']),11)
        self.assertEqual(len(doc['recent_audit_events']),13)
        self.assertEqual(doc['recent_audit_events'][0]['event_seq'],260)
        self.assertTrue(doc['capped_lists_may_be_partial'])

    def test_named_sql_plans_are_project_scoped(self):
        for key,statement in plans().items():
            operation,sql=statement
            self.assertIn(':project_id',sql,key)
            if operation=='select':
                self.assertTrue(sql.strip().upper().startswith('SELECT'))

    def test_distinct_domains_only_f_writes(self):
        self.assertEqual(set(WRITES),set(plans())-set(SELECTS))
        for sql in WRITES.values():
            self.assertFalse(any(x in sql.lower() for x in ('update effect_intents','insert into audit_events','update tasks')))

if __name__=='__main__':
    unittest.main()
