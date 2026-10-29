"""CAB-F R9 exact-DDL fixture; fake CAB-D/CAS ports are labeled simulations."""
import copy
import os
import sqlite3
import threading
import unittest
from pathlib import Path

from nomad_studio.cabals.council import CouncilService
from nomad_studio.contracts.runtime_ports_v1 import Decision, Result
from nomad_studio.cabals.council.records import digest

A='a'*64
B='b'*64
C='c'*64
D='d'*64

class LocalDB:
    """Test-only synchronous SQLite; P2 supplies the real table-limited writer."""
    simulated=True
    def __init__(self):
        self.conn=sqlite3.connect(':memory:',check_same_thread=False)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        contract_dir=os.environ.get('NOMAD_R9_CONTRACTS_DIR')
        if not contract_dir:
            raise RuntimeError('must supply NOMAD_R9_CONTRACTS_DIR (frozen R8 DDL)')
        self.conn.executescript((Path(contract_dir)/'initial_schema_v1.sql').read_text())
        self.lock=threading.RLock()
    def read(self,fn):
        with self.lock: return fn(self.conn)
    def write(self,fn):
        with self.lock:
            # Model P2's table identity capability, not a caller-defined SQL prefix.
            allowed={'v1_council_cases','v1_council_proposals','v1_council_dissent'}
            mutations={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE}
            ddl={sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_DROP_TABLE,
                 sqlite3.SQLITE_ALTER_TABLE,sqlite3.SQLITE_CREATE_TRIGGER,
                 sqlite3.SQLITE_DROP_TRIGGER,sqlite3.SQLITE_CREATE_INDEX,
                 sqlite3.SQLITE_DROP_INDEX}
            def authorize(op,table,field,dbname,source):
                if (op in mutations and table not in allowed) or op in ddl:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            self.conn.execute('SAVEPOINT cab_f_write')
            self.conn.set_authorizer(authorize)
            try:
                answer=fn(self.conn)
                self.conn.execute('RELEASE SAVEPOINT cab_f_write')
                return answer
            except BaseException:
                self.conn.execute('ROLLBACK TO SAVEPOINT cab_f_write')
                self.conn.execute('RELEASE SAVEPOINT cab_f_write')
                raise
            finally:
                self.conn.set_authorizer(None)

class MemoryCAS:
    simulated=True
    def __init__(self): self.data={}
    def put(self,record):
        h=digest(record)
        self.data.setdefault(h,copy.deepcopy(record))
        return h
    def get(self,hash):
        return copy.deepcopy(self.data.get(hash))

class BibleFake:
    simulated=True
    def __init__(self): self.pair=A; self.design='design001';self.technical='technical001'
    def snapshot_active_pair(self,project_id,scope):
        return Result('OK','OK',{'project_id':project_id,'pair_digest':self.pair,
                  'design_edition_id':self.design,'technical_edition_id':self.technical})
    def verify_clause_provenance(self,clause_refs,bible_pair_digest):
        if bible_pair_digest!=self.pair or clause_refs != ['clause001']:
            return Decision(False,'DENY','BIBLE_CONFLICT',bible_pair_digest)
        return Decision(True,'ALLOW','OK',bible_pair_digest,('fixture:verified-clause',))

class AuthorityFake:
    simulated=True
    def __init__(self): self.status='OK'; self.code='OK'; self.calls=0
    def assess_bible_conflict(self,feature_id,record,pair):
        self.calls+=1
        return Result(self.status,self.code,evidence_refs=('fixture:assessment',))


def proposal(pid='choice001',cabal='cabal001',summary='Choose shared API surface'):
    return {'proposal_id':pid,'author_cabal_id':cabal,'summary':summary,
            'clause_refs':['clause001'],'affected_resources':['system/api'],
            'dependency_refs':['feature/dependency'],
            'provenance_refs':['source/design-ref'],'action':'RESOLVE_IN_BIBLE'}

def conflict(case='case001'):
    return {'case_id':case,'project_id':'project001',
            'feature_contract_digest':B,'graph_revision':3,'kind':'API',
            'summary':'Two cabals need a shared API contract',
            'clause_refs':['clause001'],'affected_resources':['system/api'],
            'dependency_refs':['feature/dependency'],
            'provenance_refs':['source/original-brief'],
            'proposals':[proposal(),proposal('choice002','cabal002','Alternative API surface')]}

def dissent(did='dissent001'):
    return {'dissent_id':did,'author_id':'worker001','reason':'Preserve the previous interface',
             'proposal_id':'choice001','clause_refs':['clause001'],
             'evidence_refs':['source/implementation-review']}

class CouncilTests(unittest.TestCase):
    def setUp(self):
        self.db=LocalDB();self.records=MemoryCAS(); self.bible=BibleFake()
        self.authority=AuthorityFake()
        db=self.db.conn
        db.execute('INSERT INTO v1_projects VALUES (?,?,?,?,?)',('project001','owner001',A,'ACTIVE',1))
        db.execute('INSERT INTO v1_projects VALUES (?,?,?,?,?)',('project002','owner002',A,'ACTIVE',1))
        for kind, eid in [('DESIGN','design001'),('TECHNICAL','technical001')]:
            db.execute('''INSERT INTO v1_bible_editions
                (project_id,kind,edition_id,epoch,manifest_digest,source_repo,source_commit,
                owner_activation_ref,active,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                ('project001',kind,eid,1,C,'repo','a'*40,'owner-proof',1,'{}'))
        db.execute('INSERT INTO v1_briefs VALUES (?,?,?,?,?,?,?,?)',
                   ('project001','brief001','feature001','owner001','brief',A,'cmd001',1))
        db.execute('''INSERT INTO v1_feature_contracts
          (project_id,feature_id,revision,brief_id,original_brief_digest,bible_pair_digest,
           contract_digest,scope_digest,deliverables_digest,acceptance_digest,budget_limit_units,
           payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
           ('project001','feature001',1,'brief001',A,A,B,D,D,D,100,'{}'))
        db.execute('''INSERT INTO v1_feature_runs
          (project_id,feature_id,brief_id,active_contract_digest,bible_pair_digest,phase,revision,
           graph_revision,updated_at_us) VALUES (?,?,?,?,?,?,?,?,?)''',
           ('project001','feature001','brief001',B,A,'PLANNED',1,3,100))
        for cabal in ('cabal001','cabal002'):
            db.execute('INSERT INTO v1_cabals VALUES (?,?,?,?,?,?)',
                      ('project001',cabal,1,D,A,'ACTIVE'))
        db.execute('INSERT INTO v1_memberships VALUES (?,?,?,?,?)',
                   ('project001','cabal001','worker001','author',100000))
        db.commit()
        self.s=CouncilService(db=self.db,records=self.records,bible=self.bible,
                              authority=self.authority,clock_us=lambda:100)
    def tearDown(self): self.db.conn.close()
    def open(self,data=None): return self.s.open_case('feature001',data or conflict(),A)
    def allow(self,pid='choice001'):
        entry=next(p for p in conflict()['proposals'] if p['proposal_id']==pid)
        return Decision(True,'ALLOW','OK',digest(entry),('fixture:assessment',))

    def test_real_ddl_and_full_provenance_and_alternative_preserved(self):
        self.assertEqual(self.open().status,'OK')
        r=self.s.get_recommendation('case001')
        self.assertEqual(r.status,'OK')
        self.assertEqual(len(r.record['proposals']),2)
        self.assertEqual(r.record['conflict']['dependency_refs'],['feature/dependency'])
        self.assertEqual(r.record['state'],'OPEN')
        self.assertEqual(self.open().status,'OK')
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM v1_council_cases').fetchone()[0],1)

    def test_resolve_in_bible_and_dissent_never_lost(self):
        self.open()
        self.assertEqual(self.s.record_dissent('case001',dissent()).status,'OK')
        self.assertEqual(self.s.resolve_case('case001',{'proposal_id':'choice001'},self.allow()).status,'OK')
        r=self.s.get_recommendation('case001')
        self.assertEqual(r.record['state'],'RESOLVED')
        self.assertEqual(r.record['selected_proposal_id'],'choice001')
        self.assertTrue(r.record['advisory_only'])
        self.assertEqual(len(r.record['dissent']),1)
        self.assertEqual(r.record['dissent'][0]['reason'],dissent()['reason'])
        self.assertEqual(self.s.record_dissent('case001',dissent('dissent002')).status,'OK')
        self.assertEqual(len(self.s.get_recommendation('case001').record['dissent']),2)
        self.assertEqual(self.s.resolve_case('case001',{'proposal_id':'choice001'},self.allow()).status,'OK')
        self.assertEqual(self.s.resolve_case('case001',{'proposal_id':'choice002'},self.allow('choice002')).status,'CONFLICT')
        self.assertEqual(self.db.conn.execute('SELECT sum(selected) FROM v1_council_proposals').fetchone()[0],1)

    def test_forged_allow_does_not_satisfy_assessment(self):
        self.open()
        out=self.s.resolve_case('case001',{'proposal_id':'choice001'},
            Decision(True,'ALLOW','OK',digest(proposal()),('fixture:forged',)))
        self.assertEqual((out.status,out.code),('REJECTED','EVIDENCE_INVALID'))
        self.assertEqual(self.s.get_recommendation('case001').record['state'],'OPEN')

    def test_cross_scope_proposals_rejected(self):
        c=conflict();c['proposals'][0]['affected_resources']=['other/project']
        self.assertEqual(self.open(c).code,'INVALID_INPUT')
        c=conflict();c['proposals'][1]['dependency_refs']=['unknown/feature']
        self.assertEqual(self.open(c).code,'INVALID_INPUT')

    def test_protected_actions_and_untrusted_external_approval_rejected(self):
        p=proposal();p['action']='BIBLE_ACTIVATE'
        c=conflict();c['proposals'][0]=p
        self.assertEqual(self.open(c).code,'INVALID_INPUT')
        self.open()
        self.assertEqual(self.s.resolve_case('case001',{'proposal_id':'choice001',
                       'action':'PROTECTED_MAINLINE_MERGE'},self.allow()).code,'INVALID_INPUT')
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM v1_owner_decisions').fetchone()[0],0)
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM v1_independent_qa').fetchone()[0],0)

    def test_owner_amendment_routes_without_mutation(self):
        self.open();self.authority.status='REJECTED';self.authority.code='OWNER_ACTION_REQUIRED'
        out=self.s.resolve_case('case001',{'proposal_id':'choice001'},self.allow())
        self.assertEqual(out.code,'OWNER_ACTION_REQUIRED')
        self.assertEqual(self.s.get_recommendation('case001').record['state'],'OWNER_ACTION_REQUIRED')
        self.assertEqual(self.db.conn.execute('SELECT SUM(selected) FROM v1_council_proposals').fetchone()[0],0)
        record=self.s.get_recommendation('case001').record
        escalation=next(p['compliance'] for p in record['proposals'] if p['proposal']['proposal_id']=='choice001')
        self.assertEqual(escalation['code'],'OWNER_ACTION_REQUIRED')
        self.assertEqual(escalation['proposal_digest'],digest(proposal()))

    def test_retries_collisions_and_auth_boundaries(self):
        self.open()
        changed=conflict();changed['summary']='different'
        self.assertEqual(self.open(changed).code,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.s.record_dissent('case001',dissent()).status,'OK')
        self.assertEqual(self.s.record_dissent('case001',dissent()).status,'OK')
        bad=dissent();bad['reason']='rewritten'
        self.assertEqual(self.s.record_dissent('case001',bad).code,'IDEMPOTENCY_CONFLICT')
        unauthorized=dissent('dissent002');unauthorized['author_id']='intruder'
        self.assertEqual(self.s.record_dissent('case001',unauthorized).code,'PERMISSION_DENIED')
        foreign=conflict('case002');foreign['project_id']='project002'
        self.assertEqual(self.open(foreign).code,'WRONG_PROJECT')

    def test_writer_rejects_foreign_table_and_ddl(self):
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.write(lambda tx: tx.execute("UPDATE v1_projects SET state='PAUSED' WHERE project_id='project001'"))
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.write(lambda tx: tx.execute('CREATE TABLE unauthorized(id)'))
        self.assertEqual(self.db.conn.execute('SELECT state FROM v1_projects WHERE project_id=?',
                        ('project001',)).fetchone()[0],'ACTIVE')

    def test_stale_lineage_and_corrupt_archive_fail_closed(self):
        self.open()
        self.db.conn.execute('UPDATE v1_feature_runs SET graph_revision=4 WHERE feature_id=?',('feature001',))
        self.assertEqual(self.s.resolve_case('case001',{'proposal_id':'choice001'},self.allow()).code,
                         'REVISION_CONFLICT')
        self.db.conn.execute('UPDATE v1_feature_runs SET graph_revision=3 WHERE feature_id=?',('feature001',))
        self.bible.pair=D
        self.assertEqual(self.s.record_dissent('case001',dissent()).code,'STALE_BIBLE')
        self.bible.pair=A
        h=self.db.conn.execute('SELECT conflict_digest FROM v1_council_cases').fetchone()[0]
        self.records.data[h]['summary']='corrupted'
        self.assertEqual(self.s.get_recommendation('case001').code,'EVIDENCE_INVALID')

    def test_cabal_revocation_fences_selection(self):
        self.open()
        self.db.conn.execute("UPDATE v1_cabals SET state='SUSPENDED' WHERE cabal_id='cabal001'")
        outcome=self.s.resolve_case('case001',{'proposal_id':'choice001'},self.allow())
        self.assertEqual((outcome.status,outcome.code),('REJECTED','PERMISSION_DENIED'))
        self.assertEqual(self.s.get_recommendation('case001').record['state'],'OPEN')

    def test_missing_archive_fails_closed(self):
        self.open()
        digest_ref=self.db.conn.execute('SELECT conflict_digest FROM v1_council_cases').fetchone()[0]
        del self.records.data[digest_ref]
        self.assertEqual(self.s.get_recommendation('case001').status,'NOT_VERIFIED')
        self.assertEqual(self.s.record_dissent('case001',dissent()).code,'EVIDENCE_INVALID')

    def test_threaded_duplicate_resolution_serializes(self):
        self.open()
        output=[]
        def worker(p):
            output.append(self.s.resolve_case('case001',{'proposal_id':p},self.allow(p)).status)
        ts=[threading.Thread(target=worker,args=(p,)) for p in ('choice001','choice002')]
        [t.start() for t in ts];[t.join() for t in ts]
        self.assertEqual(sorted(output),['CONFLICT','OK'])
        self.assertEqual(self.db.conn.execute('SELECT SUM(selected) FROM v1_council_proposals').fetchone()[0],1)

if __name__=='__main__': unittest.main()
