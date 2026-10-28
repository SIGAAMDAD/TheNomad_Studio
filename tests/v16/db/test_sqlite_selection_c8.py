"""A06 SQL-only owner-granted next-job selection and controlled response."""
import unittest
from studio_v16.communications.service import CoordinationService, Scope
from studio_v16.gateway.interfaces import Principal
from tests.v16.db import test_sqlite_commands_c8 as fixture


class SQLiteSelectionTests(unittest.TestCase):
    setUp = fixture.SQLiteCommandsC8.setUp

    def coordination(self):
        return CoordinationService(self.store, policy={
            fixture.PRINCIPAL: Scope(frozenset({'design'}), frozenset({'RESPONSE'}))})

    def test_authenticated_sql_selector_claim_and_resource_lease(self):
        coord=self.coordination()
        self.assertEqual(coord.start_response('resp-select', fixture.WORKER,'design',fixture.CTX),'RUNNING')
        cmd=coord.next_eligible_claim('resp-select',Principal(fixture.PRINCIPAL))
        self.assertEqual(cmd['payload']['source_base_sha'],fixture.BASE)
        self.assertEqual(cmd['payload']['owner_decision_ref'],'decision_000')
        self.assertEqual(coord.claim_for_response('resp-select',cmd,fixture.CTX)['result'],'COMMITTED')
        self.assertIsNone(coord.next_eligible_claim('resp-select',Principal(fixture.PRINCIPAL)))

    def test_revoked_grant_not_selected(self):
        coord=self.coordination()
        coord.start_response('resp-revoked',fixture.WORKER,'design',fixture.CTX)
        with self.owner.write() as (conn,now):
            conn.execute("UPDATE grants SET revoked_at=? WHERE grant_id='grant_000'",(now,))
        self.assertIsNone(coord.next_eligible_claim('resp-revoked',Principal(fixture.PRINCIPAL)))

    def test_other_identity_cannot_read_selection(self):
        coord=self.coordination()
        coord.start_response('resp-other',fixture.WORKER,'design',fixture.CTX)
        with self.assertRaises(Exception):
            coord.next_eligible_claim('resp-other',Principal('unknown_worker'))


if __name__ == '__main__':
    unittest.main()
