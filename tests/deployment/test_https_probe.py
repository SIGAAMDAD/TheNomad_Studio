from contextlib import nullcontext
import importlib.util
from pathlib import Path
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('probe', ROOT/'infra/greenfield/gateway_https_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Response:
    status = 200
    def __init__(self, body):
        self.body = body
    def read(self, amount):
        return self.body
    def __enter__(self): return self
    def __exit__(self, *args): pass


class HttpsProbeTests(unittest.TestCase):
    def invoke(self, response):
        with mock.patch.object(probe.ssl, 'create_default_context') as ctx:
            ctx.return_value.load_cert_chain.return_value = None
            return probe.run(['--url', 'https://bridge.example', '--ca-file', '/example/ca',
                              '--client-cert', '/example/cert', '--client-key', '/example/key'],
                             opener=lambda *args, **kwargs: Response(response), challenge_factory=lambda: 'hello')

    def test_host_tls_probe_still_does_not_verify_chat(self):
        with mock.patch.object(probe, 'print') as writer:
            rc = self.invoke(b'{"version":"NOMAD/RECEIPT/1","challenge":"hello","authenticated":true}')
            self.assertEqual(rc, 0)
            self.assertIn('"real_chat_verified": false', writer.call_args.args[0])

    def test_replay_duplicate_key_and_oversized_reply_denied(self):
        for body in (
            b'{"version":"NOMAD/RECEIPT/1","challenge":"wrong","authenticated":true}',
            b'{"version":"NOMAD/RECEIPT/1","challenge":"hello","challenge":"hello","authenticated":true}',
            b'X'*5000,
        ):
            with self.subTest(body=body[:15]):
                self.assertEqual(self.invoke(body), 1)

    def test_no_tls_material_prevents_any_network_request(self):
        with mock.patch.object(probe.ssl, 'create_default_context', side_effect=FileNotFoundError('ca')):
            self.assertEqual(probe.run(['--url', 'https://bridge.example', '--ca-file', '/missing',
                        '--client-cert', '/missing', '--client-key', '/missing'], opener=lambda *a, **k: self.fail('should not open')), 2)
