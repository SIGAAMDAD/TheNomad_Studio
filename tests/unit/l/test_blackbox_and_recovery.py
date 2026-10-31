"""Black-box verifier mutation tests with disposable loopback fake service.

Fake service proves L's probe detects isolation failures; NOT a Tier B Studio PASS.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import threading
import unittest
from qa.blackbox import project_isolation_probe
from qa.runtime import run_scenario, RuntimeErrorQA

class ScopeService(BaseHTTPRequestHandler):
    vulnerable = False
    def do_GET(self):
        authorization=self.headers.get('Authorization')
        pid=self.path.split('/')[-1]
        expected='Token-A' if pid=='A' else 'Token-B'
        allowed = authorization==expected or self.__class__.vulnerable
        self.send_response(200 if allowed else 403)
        self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(b'{"state":"active"}' if allowed else b'{"error":"forbidden"}')
    def log_message(self,*args): pass

class Probes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=HTTPServer(('127.0.0.1',0),ScopeService)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def test_two_project_fixture_positive(self):
        os.environ['QA_TEST_TOKEN_A']='Token-A'
        os.environ['QA_TEST_TOKEN_B']='Token-B'
        try:
            cfg={'base_url':self.base,'project_a':'A','project_b':'B',
                 'token_a_env':'QA_TEST_TOKEN_A','token_b_env':'QA_TEST_TOKEN_B'}
            proof=project_isolation_probe(cfg)
            self.assertTrue(proof['success'])
            self.assertEqual(['A->A','A->B','B->B','B->A'], [step['step'] for step in proof['observations']])
            ScopeService.vulnerable = True
            self.assertFalse(project_isolation_probe(cfg)['success'])
        finally:
            ScopeService.vulnerable=False
            del os.environ['QA_TEST_TOKEN_A'];del os.environ['QA_TEST_TOKEN_B']

    def test_same_scoped_token_invalid_fixture(self):
        with self.assertRaises(ValueError):
            project_isolation_probe({'base_url':self.base,'project_a':'A','project_b':'B',
                                     'token_a_env':'ONE','token_b_env':'ONE'})

    def test_unauthorized_project_id_traversal(self):
        with self.assertRaises(ValueError):
            project_isolation_probe({'base_url':self.base,'project_a':'../A','project_b':'B',
                                     'token_a_env':'ONE','token_b_env':'TWO'})

    def test_http_byte_equivalence(self):
        url=self.base+'/v1/projects/A'
        os.environ['QA_TEST_TOKEN_A']='Token-A'
        try:
            case={'id':'recovery','steps':[
                {'type':'http','url':url,'headers_from_env':{'Authorization':'QA_TEST_TOKEN_A'},'expect_status':[200]},
                {'type':'http','url':url,'headers_from_env':{'Authorization':'QA_TEST_TOKEN_A'},'expect_status':[200], 'expect_same_body_as_step':0}
            ]}
            self.assertEqual('PASS',run_scenario(case)['status'])
            case['steps'][1]['expect_different_body_from_step']=0
            self.assertEqual('FAIL',run_scenario(case)['status'])
        finally:
            del os.environ['QA_TEST_TOKEN_A']

    def test_future_body_comparison_invalid(self):
        with self.assertRaises(RuntimeErrorQA):
            run_scenario({'id':'invalid', 'steps':[{'type':'http','url':self.base+'/v1/projects/A',
                                                  'expect_status':[403], 'expect_same_body_as_step':1}]})

if __name__=='__main__': unittest.main()
