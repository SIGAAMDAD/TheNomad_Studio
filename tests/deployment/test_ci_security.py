import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(script):
    spec = importlib.util.spec_from_file_location(script, ROOT/'infra'/'greenfield'/f'{script}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CiSecurityTests(unittest.TestCase):
    def test_missing_modules_make_integrated_ci_red(self):
        gate = load('ci_gate')
        with tempfile.TemporaryDirectory() as d:
            failures = gate.check(Path(d))
            self.assertGreaterEqual(len(failures), 20)
            self.assertIn('missing R9 source or test: src/nomad_studio/trust/api.py', failures)

    def test_security_scan_detects_and_does_not_exfiltrate(self):
        scan = load('security_scan')
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'a.pem').write_text('-----BEGIN ' + 'PRIVATE KEY-----\n')
            problems = scan.scan(Path(d))
            self.assertIn(('a.pem', 'private-key'), problems)
        self.assertEqual(scan.scan(ROOT), [])

    def test_probe_invalid_origin_does_not_send_request(self):
        probe = load('gateway_https_probe')
        self.assertEqual(probe.run(['--url', 'http://localhost', '--ca-file', 'x',
                                    '--client-cert','x','--client-key','x']), 2)


class DeploymentFilesTests(unittest.TestCase):
    def test_systemd_and_proxy_remain_private(self):
        unit = (ROOT/'infra/greenfield/systemd/nomad-studio.service').read_text()
        nginx = (ROOT/'infra/greenfield/nginx/nomad-studio-mtls.conf.template').read_text()
        integrated = (ROOT/'.github/workflows/greenfield-integrated.yml').read_text()
        self.assertIn('UMask=0077', unit)
        self.assertIn('ProtectSystem=strict', unit)
        self.assertIn('NoNewPrivileges=yes', unit)
        self.assertIn('ssl_verify_client on;', nginx)
        self.assertIn('proxy_pass http://127.0.0.1:8765;', nginx)
        self.assertIn('ci_gate.py', integrated)
        self.assertIn('ci_run_all.py', integrated)
        self.assertNotIn('continue-on-error:', integrated)
