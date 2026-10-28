"""Real local bare Git remote CAS; Drive injected atomic fake."""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from studio_v16.providers import IsolatedGitProvider, DriveRevisionProvider, ProviderBlocked


def run(root,*args):
    return subprocess.check_output(('git',*args),cwd=root).decode().strip()

class GitProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.bare=root/'remote.git';self.src=root/'source'
        subprocess.check_call(('git','init','-q','--bare',str(self.bare)))
        self.src.mkdir();run(self.src,'init','-q');run(self.src,'config','user.name','tester');run(self.src,'config','user.email','test@example.invalid')
        (self.src/'README').write_text('one\n')
        run(self.src,'add','README');run(self.src,'commit','-qm','base')
        self.base=run(self.src,'rev-parse','HEAD')
        run(self.src,'branch','-M','studio/workorders/WORK-001/implement')
        run(self.src,'remote','add','origin',str(self.bare))
        run(self.src,'push','-q','origin','HEAD:refs/heads/studio/workorders/WORK-001/implement')
        (self.src/'README').write_text('two\n')
        self.patch=subprocess.check_output(('git','diff','--binary'),cwd=self.src)
        self.repo='SIGAAMDAD/NomadFramework';self.branch='studio/workorders/WORK-001/implement'
        self.reader=IsolatedGitProvider({self.repo:str(self.bare)})
        self.inspected=self.reader.inspect_patch(self.repo,self.base,self.patch)
        self.intent={'publication_id':'pub-one','idempotency_key':'a'*64,
                     'artifact_sha256':hashlib.sha256(self.patch).hexdigest(),
                     'target':{'provider':'GITHUB','repo_id':self.repo,
                       'work_branch':self.branch,'source_base_sha':self.base,
                       'proposed_tree_sha256':self.inspected.proposed_tree_sha256,
                       'patch_sha256':hashlib.sha256(self.patch).hexdigest(),
                       'changed_paths':list(self.inspected.changed_paths)}}

    def test_inspect_exact_source_patch(self):
        s=self.reader.snapshot(self.repo,self.branch)
        self.assertEqual(self.base,s.head_sha)
        self.assertEqual(('README',),self.inspected.changed_paths)
        self.assertNotEqual('0'*64,self.inspected.proposed_tree_sha256)

    def test_publication_disabled(self):
        with self.assertRaisesRegex(ProviderBlocked,'CUTOVER'):
            self.reader.publish(self.intent,self.patch)
        self.assertEqual(self.base,self.reader.snapshot(self.repo,self.branch).head_sha)

    def test_atomic_cas_and_dedup(self):
        writer=IsolatedGitProvider({self.repo:str(self.bare)},authorized=lambda x:True,enable_external_effects=True)
        receipt=writer.publish(self.intent,self.patch)
        self.assertEqual(receipt,writer.readback(self.intent))
        self.assertNotEqual(self.base,receipt['commit_sha'])
        self.assertEqual(receipt,writer.publish(self.intent,self.patch))
        self.assertEqual(receipt['commit_sha'],writer.snapshot(self.repo,self.branch).head_sha)

    def test_push_timeout_after_effect_is_read_back(self):
        from unittest.mock import patch
        import studio_v16.providers.git_provider as mod
        writer=IsolatedGitProvider({self.repo:str(self.bare)},authorized=lambda x:True,enable_external_effects=True)
        actual=mod.git
        once={'done':False}
        def interrupted(cwd,*args,**kwargs):
            result=actual(cwd,*args,**kwargs)
            if args and args[0]=='push' and not once['done']:
                once['done']=True
                raise ProviderBlocked('GIT_COMMAND_FAILED')
            return result
        with patch.object(mod,'git',side_effect=interrupted):
            result=writer.publish(self.intent,self.patch)
        self.assertEqual(result,writer.readback(self.intent))
        self.assertTrue(once['done'])

    def test_conflict_does_not_move_branch(self):
        (self.src/'README').write_text('someone else\n')
        run(self.src,'add','README');run(self.src,'commit','-qm','racer')
        run(self.src,'push','-q','origin','HEAD:refs/heads/'+self.branch)
        head=self.reader.snapshot(self.repo,self.branch).head_sha
        writer=IsolatedGitProvider({self.repo:str(self.bare)},authorized=lambda x:True,enable_external_effects=True)
        with self.assertRaisesRegex(ProviderBlocked,'REBASE_REQUIRED'):
            writer.publish(self.intent,self.patch)
        self.assertEqual(head,self.reader.snapshot(self.repo,self.branch).head_sha)

class CASDrive:
    def __init__(self):
        self.rev='1';self.content=b'old';self.writes=0;self.key=None
    def revision(self,file_id):return self.rev
    def readback(self,file_id):return {'revision':self.rev,'content':self.content,'idempotency_key':self.key}
    def update_if_revision(self,file_id,rev,content,key):
        if rev!=self.rev:raise ValueError('conflict')
        self.rev='2';self.content=content;self.key=key;self.writes+=1

class DriveTests(unittest.TestCase):
    def setUp(self):
        self.cas=CASDrive();self.payload=b'new content'
        self.intent={'idempotency_key':'b'*64,'target':{'file_id':'file','expected_revision':'1',
                    'exported_sha256':hashlib.sha256(self.payload).hexdigest()}}
    def test_disabled(self):
        with self.assertRaisesRegex(ProviderBlocked,'CUTOVER'):
            DriveRevisionProvider(self.cas).publish(self.intent,self.payload)
    def test_cas_readback_and_no_duplicate(self):
        s=DriveRevisionProvider(self.cas,authorized=lambda i:True,enable_external_effects=True)
        first=s.publish(self.intent,self.payload)
        self.assertEqual('2',first['revision'])
        self.assertEqual(first,s.publish(self.intent,self.payload))
        self.assertEqual(1,self.cas.writes)
    def test_timeout_after_atomic_write_reads_back(self):
        class TimedOut(CASDrive):
            def update_if_revision(self,*args):
                super().update_if_revision(*args)
                raise TimeoutError('provider dropped response')
        client=TimedOut()
        s=DriveRevisionProvider(client,authorized=lambda i:True,enable_external_effects=True)
        self.assertEqual('2',s.publish(self.intent,self.payload)['revision'])
        self.assertEqual(1,client.writes)

    def test_same_content_from_other_writer_is_not_our_effect(self):
        self.cas.rev='2'
        self.cas.content=self.payload
        self.cas.key='different-idempotency-key'
        s=DriveRevisionProvider(self.cas,authorized=lambda i:True,enable_external_effects=True)
        self.assertIsNone(s.readback(self.intent))
        with self.assertRaisesRegex(ProviderBlocked,'DOCUMENT_CONFLICT'):
            s.publish(self.intent,self.payload)

    def test_revision_conflict(self):
        self.cas.rev='4'
        s=DriveRevisionProvider(self.cas,authorized=lambda i:True,enable_external_effects=True)
        with self.assertRaisesRegex(ProviderBlocked,'DOCUMENT_CONFLICT'):
            s.publish(self.intent,self.payload)

if __name__=='__main__':unittest.main()
