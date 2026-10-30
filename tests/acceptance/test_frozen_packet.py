"""Actual frozen packet acceptance, Tier A only.

`NOMAD_R5_PACKET` points to the unmodified extracted plan ZIP contents.
Passing these tests is NOT proof of the Studio code or GitHub transport.
"""
import os
import sqlite3
import unittest
from pathlib import Path
from qa.contract import load_plan, pinned_contract_digests, EXPECTED_PLAN_SHA
from qa.core import digest, read_json

PACKET = Path(os.environ.get('NOMAD_R5_PACKET', '/nonexistent/r5-packet'))

@unittest.skipUnless((PACKET/'plan.json').is_file(), 'frozen packet not mounted')
class FrozenAcceptance(unittest.TestCase):
    def test_one_wave_thirteen_distinct_groups(self):
        plan=load_plan(PACKET)
        self.assertEqual(1, len(plan['waves']))
        self.assertEqual(13, len(plan['workgroups']))
        self.assertEqual(EXPECTED_PLAN_SHA,digest((PACKET/'plan.json').read_bytes()))

    def test_read_actual_contract_bytes(self):
        digests=pinned_contract_digests(PACKET)
        self.assertIn('CONTRACTS/initial_schema.sql',digests)
        self.assertIn('CONTRACTS/openapi.json',digests)
        self.assertIn('CONTRACTS/host_protocol.schema.json',digests)
        self.assertTrue(all(len(h)==64 for h in digests.values()))

    def test_required_api_paths_exact_methods(self):
        api=read_json(PACKET/'CONTRACTS/openapi.json')
        for method, path, op in [('post','/v1/projects/{project_id}/workers/start','startWorker'),
                                 ('post','/v1/projects/{project_id}/relays','sendRelay'),
                                 ('post','/v1/projects/{project_id}/integrations/{intent_id}/promote','promoteIntegration'),
                                 ('post','/v1/projects/{project_id}/reviews/{review_id}/verdict','submitReview')]:
            self.assertEqual(op,api['paths'][path][method]['operationId'])

    def test_reference_schema_rejects_cross_project_grants(self):
        db=sqlite3.connect(':memory:')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript((PACKET/'CONTRACTS/initial_schema.sql').read_text())
        db.execute('INSERT INTO principals VALUES (?,?,?,?)', ('pr','test','pr','human'))
        db.execute('INSERT INTO projects(project_id,name,charter_digest,state) VALUES (?,?,?,?)', ('one','One','a','active'))
        db.execute('INSERT INTO projects(project_id,name,charter_digest,state) VALUES (?,?,?,?)', ('two','Two','b','active'))
        db.execute('INSERT INTO project_grants VALUES (?,?,?)',('one','pr','owner'))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute('INSERT INTO project_grants VALUES (?,?,?)',('three','pr','owner'))
        self.assertEqual(0, db.execute("SELECT COUNT(*) FROM project_grants WHERE project_id='two'").fetchone()[0])
        db.close()

    def test_untrusted_duplicate_json_keys_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'message.json';p.write_text('{"project_id":"one","project_id":"two"}')
            with self.assertRaises(ValueError):read_json(p)

if __name__=='__main__': unittest.main()
