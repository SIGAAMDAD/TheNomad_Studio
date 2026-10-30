"""Positive/negative proof tests for byte-complete handoff and skill readiness."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from qa.core import digest, source_files, tree_digest
from qa.contract import EXPECTED_PLAN_SHA, pinned_contract_digests
from qa.submissions import zip_contents, verify_one, SubmissionError
from qa.roles import verify_roles
from qa.receipts import validate_review, ReceiptError

PACKET = Path(__file__).resolve().parents[3] / 'QA_PACKET_DOES_NOT_EXIST'
PLAN = {'workgroups': [{'id':f'WG-{c}', 'owned_paths':['qa/**'] if c=='L' else [f'src/{c}/**']} for c in 'ABCDEFGHIJKLM']}

class SubmissionTests(unittest.TestCase):
    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            arc=Path(td)/'bad.zip'
            with zipfile.ZipFile(arc,'w') as z:
                z.writestr('../escape.py', b'bad')
            with self.assertRaises(ValueError): zip_contents(arc)

    def test_duplicate_entries_rejected(self):
        import warnings
        with tempfile.TemporaryDirectory() as td:
            arc=Path(td)/'bad.zip'
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(arc,'w') as z:
                    z.writestr('qa/a.py', b'a')
                    z.writestr('qa/a.py', b'b')
            with self.assertRaises(SubmissionError): zip_contents(arc)

    def test_wrong_worker_rejected_even_with_good_content(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); packet=root/'packet'; subs=root/'sub'; packet.mkdir(); subs.mkdir()
            (packet/'CONTRACTS').mkdir()
            (packet/'CONTRACTS'/'file').write_text('frozen')
            with zipfile.ZipFile(subs/'WG-L.zip','w') as z:
                z.writestr('src/M/hijack.py', b'not owned')
            log=subs/'real.log'; log.write_bytes(b'0 tests?')
            data={'worker':'WG-L', 'baseline_plan_sha256':EXPECTED_PLAN_SHA, 'baseline_source_tree_sha256':tree_digest({}),
                  'files': {'src/M/hijack.py':digest(b'not owned')},
                  'contracts': pinned_contract_digests(packet),
                  'test_evidence':[{'command':['python','-m','unittest'], 'exit_code':0,
                                    'log_file':'real.log', 'log_sha256':digest(log.read_bytes())}]}
            (subs/'WG-L.json').write_text(json.dumps(data))
            with self.assertRaises(ValueError): verify_one(packet, PLAN, subs, 'WG-L')

    def test_log_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); packet=root/'packet'; subs=root/'sub'; packet.mkdir(); subs.mkdir()
            (packet/'CONTRACTS').mkdir()
            with zipfile.ZipFile(subs/'WG-L.zip','w') as z:
                z.writestr('qa/proof.py', b'owned')
            log=subs/'real.log'; log.write_bytes(b'log changed')
            data={'worker':'WG-L','baseline_plan_sha256':EXPECTED_PLAN_SHA, 'baseline_source_tree_sha256':tree_digest({}),
                  'files':{'qa/proof.py':digest(b'owned')}, 'contracts':{},
                  'test_evidence':[{'command':['python','-m','unittest'], 'exit_code':0,
                                    'log_file':'real.log', 'log_sha256':digest(b'different')} ]}
            (subs/'WG-L.json').write_text(json.dumps(data))
            with self.assertRaises(SubmissionError): verify_one(packet, PLAN, subs, 'WG-L')

class RoleTests(unittest.TestCase):
    def test_real_bytes_and_smoke_logs(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'roles').mkdir(); (root/'logs').mkdir()
            (root/'roles/test.md').write_bytes(b'the installed skill')
            (root/'logs/run.txt').write_bytes(b'PASS')
            inv=root/'inventory.json'
            role={'role_id':'role1', 'skill_files':{'roles/test.md':digest(b'the installed skill')},
                  'smoke':{'argv':['python','-V'],'exit_code':0,'log_file':'logs/run.txt', 'log_sha256':digest(b'PASS')}}
            inv.write_text(json.dumps({'roles':[role]}))
            v=verify_roles(root, inv, target=2)
            self.assertEqual(1,v['ready']); self.assertEqual(1,v['missing_count'])
            (root/'roles/test.md').write_bytes(b'tamper')
            self.assertTrue(verify_roles(root, inv, target=2)['defects'])

class ReviewerReceiptTests(unittest.TestCase):
    def test_independent_byte_bound_review(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'artifact').write_bytes(b'example output')
            (root/'log').write_bytes(b'actual test transcript')
            doc={'candidate_tree_sha256':'sha-candidate','source_revision_id':'rev',
                 'submission_id':'sub','acceptance_digest':'a','author_principal_id':'author',
                 'reviewer_principal_id':'reviewer','verdict':'PASS','validator_digest':'validator',
                 'receipts':[{'artifact_path':'artifact','artifact_sha256':digest(b'example output'),
                              'log_path':'log','log_sha256':digest(b'actual test transcript'),
                              'command':['python','-m','unittest'], 'exit_code':0, 'validator_digest':'validator'}]}
            (root/'review.json').write_text(json.dumps(doc))
            self.assertTrue(validate_review('sha-candidate',root/'review.json',root)['verified_receipts'])
            with self.assertRaises(ReceiptError): validate_review('wrong-tree',root/'review.json',root)
            doc['reviewer_principal_id']='author'
            (root/'review.json').write_text(json.dumps(doc))
            with self.assertRaises(ReceiptError): validate_review('sha-candidate',root/'review.json',root)

if __name__ == '__main__': unittest.main()

# A small fully assembled positive fixture verifies that 13 signed-by-bytes
# submissions succeed, rather than only testing rejection paths.
class CompleteMergeTests(unittest.TestCase):
    def test_thirteen_distinct_archives_match_exact_merge(self):
        from qa.submissions import verify_all
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); packet=root/'packet'; subs=root/'sub'; merged=root/'merged'
            packet.mkdir();subs.mkdir();merged.mkdir()
            (packet/'CONTRACTS').mkdir()
            plan={'workgroups':[{'id':f'WG-{c}', 'owned_paths':[f'src/{c}/**']}
                                for c in 'ABCDEFGHIJKLM']}
            for c in 'ABCDEFGHIJKLM':
                worker=f'WG-{c}'; rel=f'src/{c}/owned.txt';content=f'actual {c}'.encode()
                target=merged/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
                with zipfile.ZipFile(subs/f'{worker}.zip','w') as z: z.writestr(rel,content)
                log_name=f'{worker}-test.log';log=subs/log_name;log.write_bytes(f'{c} test passed'.encode())
                info={'worker':worker,'baseline_plan_sha256':EXPECTED_PLAN_SHA,
                      'baseline_source_tree_sha256':tree_digest({}), 'files':{rel:digest(content)},
                      'contracts':{},'test_evidence':[{'command':['python','-m','unittest'],
                      'exit_code':0,'log_file':log_name,'log_sha256':digest(log.read_bytes())}]}
                (subs/f'{worker}.json').write_text(json.dumps(info))
            self.assertEqual(13,len(verify_all(packet,plan,subs,merged)['verified_groups']))
            (merged/'unexpected.py').write_text('unreviewed source')
            with self.assertRaises(SubmissionError):verify_all(packet,plan,subs,merged)
