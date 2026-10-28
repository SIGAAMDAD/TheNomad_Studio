"""A04: exact subject source proof tests through ResponseAccess and SQLite."""
import unittest

from studio_v16.db.authority_contract import GatewayContext
from studio_v16.gateway.interfaces import Principal, SourceSnapshot
from studio_v16.gateway.gateway import AdmissionError
from studio_v16.gateway.response_access import ResponseAccess
from studio_v16.gateway.source_verification import SourceBaseVerifier, SourceRoute
from tests.v16.db import test_sqlite_commands_c8 as fixture


class FakeGit:
    def __init__(self):
        self.head = fixture.BASE
        self.available = True
        self.calls = 0

    def snapshot(self, repo, branch):
        self.calls += 1
        if not self.available:
            raise ConnectionError('provider unavailable')
        return SourceSnapshot(repo, branch, self.head, ())


class AccessTests(unittest.TestCase):
    claim = fixture.SQLiteCommandsC8.claim

    def setUp(self):
        fixture.SQLiteCommandsC8.setUp(self)
        self.git = FakeGit()
        self.route = SourceRoute(fixture.JOB, 'SIGAAMDAD/TheNomad_Studio',
                                 'studio/workorders/test', fixture.BASE,
                                 fixture.PRINCIPAL, fixture.WORKER, 'grant_000', 1, 'rev_0001')
        self.verifier = SourceBaseVerifier(self.git, {fixture.JOB: self.route})
        self.store.source_verifier = self.verifier
        self.cmd = self.claim()

    def access(self):
        class Port:
            def __init__(self, store):
                self.store = store
            def claim_for_response(self, rid, cmd, ctx):
                return self.store.execute(cmd, ctx)
        return ResponseAccess(Port(self.store), Principal(fixture.PRINCIPAL),
                              eligible_command=lambda rid, p: self.cmd,
                              source_verifier=self.verifier)

    def test_two_live_readbacks_before_transaction_commit(self):
        result = self.access().next_claim('response-test')
        self.assertEqual(result['claim_receipt']['result'], 'COMMITTED')
        self.assertGreaterEqual(self.git.calls, 2)

    def test_provider_head_move_denied_and_no_attempt(self):
        self.git.head = 'f' * 40
        with self.assertRaisesRegex(AdmissionError, 'REBASE_REQUIRED'):
            self.access().next_claim('response-test')
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM attempts').fetchone()[0], 0)

    def test_provider_outage_fails_closed(self):
        self.git.available = False
        with self.assertRaisesRegex(AdmissionError, 'GATEWAY_UNAVAILABLE'):
            self.access().next_claim('response-test')

    def test_stale_route_epoch_denied(self):
        self.cmd['assignment_epoch'] = 2
        with self.assertRaisesRegex(AdmissionError, 'REBASE_REQUIRED'):
            self.access().next_claim('response-test')

    def test_forged_context_flag_cannot_bypass_tx_proof(self):
        malicious = GatewayContext(fixture.PRINCIPAL, verified_gateway=True, verified_source_base=True)
        result = self.store.execute(self.cmd, malicious)
        self.assertEqual(result['denial_code'], 'REBASE_REQUIRED')
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM attempts').fetchone()[0], 0)

    def test_git_ref_advances_between_preflight_and_claim(self):
        proof = self.verifier.verify(self.cmd, fixture.PRINCIPAL)
        self.git.head = 'f' * 40
        forged = GatewayContext(fixture.PRINCIPAL, verified_gateway=True,
                                verified_source_base=True, source_proof=proof)
        self.assertEqual(self.store.execute(self.cmd, forged)['denial_code'], 'REBASE_REQUIRED')


if __name__ == '__main__':
    unittest.main()
