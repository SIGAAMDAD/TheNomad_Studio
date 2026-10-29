import unittest
from nomad_studio.contracts.v1 import CommandEnvelope, QueryEnvelope, Outcome

class FrozenSeedContractTests(unittest.TestCase):
    def test_envelopes_exist(self):
        c=CommandEnvelope(version='NOMAD/CMD/1', request_id='r', domain='shift', operation='start', idempotency_key='x')
        self.assertEqual(c.domain, 'shift')
        self.assertEqual(Outcome(state='NOT_RUN',code='UNIMPLEMENTED').state, 'NOT_RUN')
        self.assertEqual(QueryEnvelope(version='NOMAD/QUERY/1', request_id='q', domain='state', operation='health').limit, 100)
