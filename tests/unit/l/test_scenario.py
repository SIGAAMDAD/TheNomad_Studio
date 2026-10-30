"""Local mock *harness* tests; must never be reported as real Tier B studio tests."""
from http.server import HTTPServer, BaseHTTPRequestHandler
import tempfile
import threading
import unittest
from pathlib import Path
from qa.core import digest
from qa.runtime import execute_step, run_scenario, RuntimeErrorQA

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(403 if self.path=='/cross-project' else 200)
        self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(b'{"allowed":false}' if self.path=='/cross-project' else b'{"allowed":true}')
    def log_message(self,*args): pass

class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=HTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def test_real_local_http_expectation(self):
        t={'type':'http','url':self.base+'/cross-project', 'expect_status':[403],
           'expect_json_equals':{'allowed':False}}
        self.assertTrue(execute_step(t)['success'])
        t['expect_status']=[200]
        self.assertFalse(execute_step(t)['success'])

    def test_failing_step_blocks_following_steps(self):
        c={'id':'negative','steps':[{'type':'http','url':self.base+'/cross-project','expect_status':[200]},
                                   {'type':'http','url':self.base+'/okay','expect_status':[200]}]}
        outcome=run_scenario(c)
        self.assertEqual('FAIL',outcome['status']);self.assertEqual(1,outcome['steps_attempted'])

    def test_empty_scenario_denied(self):
        with self.assertRaises(RuntimeErrorQA): run_scenario({'id':'fake-pass','steps':[]})

    def test_process_requires_opt_in(self):
        with self.assertRaises(PermissionError): execute_step({'type':'process','argv':['python','-V'],'expect_exit':[0]})

    def test_artifact_byte_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'x';path.write_bytes(b'exact bytes')
            self.assertTrue(execute_step({'type':'artifact','path':str(path),'sha256':digest(b'exact bytes')})['success'])
            self.assertFalse(execute_step({'type':'artifact','path':str(path),'sha256':'0'*64})['success'])

if __name__=='__main__':unittest.main()
