import unittest
from pathlib import Path

from studio_v16.adapters import DurableRuntime, RuntimeBlocked, PostgresAuthority
from studio_v16.db.store import GatewayContext


class Store:
    def __init__(self):
        self.records={}
        self.calls=[]
    def shadow_import(self, ident, kind, ref, revision, document, ctx):
        assert ctx.verified_gateway
        from studio_v16.adapters.runtime import digest
        key=kind,ref,revision
        d=digest(document)
        if key in self.records and self.records[key]!=d:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        self.records[key]=d
        return d
    def reconcile_shadow(self,kind,ref,revision,expected,ctx):
        assert ctx.provider_readback_verified
        return 'MATCH' if self.records[kind,ref,revision]==expected else 'MISMATCH'
    def execute(self,cmd,ctx):
        self.calls.append((cmd,ctx))
        return dict(result='COMMITTED')
    def save_resume_capsule(self,capsule,ctx):
        self.calls.append(('CAPSULE',capsule))
    def load_resume_capsule(self,job,ctx):
        return {'job_id':job}
    def resume_capsule(self,job,ctx):
        return dict(attempt_id='a',worker_id='w',assignment_epoch=1,
                    lease_generation=1,checkpoint_sha256='a'*64)

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.db=Store()
        self.service=DurableRuntime(self.db,lambda:GatewayContext('trusted',True,True))
    def test_shadow_idempotent_and_compare(self):
        a=self.service.import_snapshot('LEDGER','studio/jobs.json','sha1',{'jobs':[]})
        self.assertEqual(a.digest,self.service.import_snapshot('LEDGER','studio/jobs.json','sha1',{'jobs':[]}).digest)
        self.assertEqual('MATCH',self.service.compare_readback(a,lambda *args:({'jobs':[]},'sha1')).reconciliation)
        self.assertEqual('MISMATCH',self.service.compare_readback(a,lambda *args:({'jobs':[1]},'sha1')).reconciliation)
    def test_snapshot_revision_is_fenced(self):
        a=self.service.import_snapshot('LEDGER','j','sha1',{})
        with self.assertRaisesRegex(RuntimeBlocked,'REVISION'):
            self.service.compare_readback(a,lambda *args:({},'sha2'))
    def test_untrusted_context_rejected(self):
        svc=DurableRuntime(self.db,lambda:GatewayContext('client'))
        with self.assertRaisesRegex(RuntimeBlocked,'IDENTITY'):
            svc.import_snapshot('LEDGER','a','1',{})
    def test_ack_passthrough_fences(self):
        self.service.ack(job_id='job',worker_id='worker',attempt_id='attempt',epoch=7,
                         revision='policy1',event_id='evt_abc',effect_name='read',command_id='id1')
        cmd=self.db.calls[-1][0]
        self.assertEqual('ACK',cmd['command_kind'])
        self.assertEqual(7,cmd['payload']['delivery_assignment_epoch'])
    def test_checkpoint_capsule_mismatch_fails(self):
        with self.assertRaisesRegex(RuntimeBlocked,'CAPSULE_FENCE'):
            self.service.checkpoint(dict(command_kind='CHECKPOINT',job_id='job',attempt_id='a',
                            assignment_epoch=1),dict(job_id='different'))
    def test_report_card_is_bounded(self):
        self.assertEqual(['a','b'],[v['job_id'] for v in self.service.report_card(['b','a'])['jobs']])
        with self.assertRaises(RuntimeBlocked): self.service.report_card(['x','x'])
    def test_legacy_events_are_observations_only(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'events/2026/10/27/evt-'
            good=dest.with_name('evt-'+'a'*32+'.json')
            good.parent.mkdir(parents=True)
            good.write_text(json.dumps({'event_id':good.stem,'delivery_authority':'ARCHIVE_ONLY'}))
            self.assertEqual(1,len(self.service.observe_archived_events(Path(d),'hash-v1')))
            good.write_text(json.dumps({'event_id':good.stem,'delivery_authority':'ACTIVE'}))
            with self.assertRaisesRegex(RuntimeBlocked,'INVALID_ARCHIVED_EVENT'):
                self.service.observe_archived_events(Path(d),'hash-v2')

    def test_postgres_authority_fails_closed(self):
        a=PostgresAuthority(self.db)
        with self.assertRaises(LookupError): a.load('job','grant')
        self.assertIsNone(a.recorded('pub','key'))
        self.assertEqual('GATEWAY_UNAVAILABLE',a.reserve({},'p','r','sha'))
        self.assertFalse(a.reconcile('p','key','r',{}))

if __name__=='__main__': unittest.main()
