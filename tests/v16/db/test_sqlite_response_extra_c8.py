"""Fail-closed reproduction of missing source verification in response claims."""
import sqlite3
import unittest

from studio_v16.adapters.response import run_response
from studio_v16.communications.service import CoordinationService, Scope
from studio_v16.gateway.gateway import AdmissionError
from studio_v16.gateway.interfaces import Principal
from studio_v16.gateway.response_access import ResponseAccess
from tests.v16.db import test_sqlite_commands_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteResponseExtraC8(unittest.TestCase):
    setUp = fixture.SQLiteCommandsC8.setUp
    claim = fixture.SQLiteCommandsC8.claim

    def test_response_claim_without_verified_source_base_fails_closed(self):
        coord = CoordinationService(self.store, policy={
            fixture.PRINCIPAL: Scope(frozenset({'design'}), frozenset({'RESPONSE'})),
        })
        port = ResponseAccess(coord, Principal(fixture.PRINCIPAL), eligible_command=lambda *_: self.claim())
        self.assertEqual(port.start('resp-stop', fixture.WORKER, 'design'), 'RUNNING')
        with self.assertRaises(AdmissionError) as error:
            port.next_claim('resp-stop')
        self.assertEqual(error.exception.code, 'REBASE_REQUIRED')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM resource_leases').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_job_starts').fetchone()[0], 0)

    def test_response_yields_with_claim_failure_and_no_false_completion(self):
        coord = CoordinationService(self.store, policy={
            fixture.PRINCIPAL: Scope(frozenset({'design'}), frozenset({'RESPONSE'})),
        })
        port = ResponseAccess(coord, Principal(fixture.PRINCIPAL), eligible_command=lambda *_: self.claim())
        worked = []
        result = run_response(port, 'resp-yield', fixture.WORKER, 'design',
                              lambda job: worked.append(job), lambda: {'checkpoint': 'unstarted'})
        self.assertEqual(worked, [])
        self.assertEqual(result['termination'], 'RESOURCE_BOUNDARY_YIELD')
        self.assertEqual(result['jobs'], [])
        self.assertEqual(result['failures'], [{'stage': 'CLAIM', 'error_type': 'AdmissionError'}])
        self.assertEqual(result['independent_qa'], 'NOT_ATTESTED')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute("SELECT status FROM response_attempts WHERE response_id='resp-yield'").fetchone()[0], 'YIELDED')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
