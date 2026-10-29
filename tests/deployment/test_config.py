import tempfile
from pathlib import Path
import unittest

from nomad_studio.deployment.config import DeploymentConfig, ConfigurationError, secure_file, private_dir


class ConfigTests(unittest.TestCase):
    def test_default_requires_external_settings(self):
        c = DeploymentConfig.from_mapping({})
        self.assertEqual(c.gateway_host, '127.0.0.1')
        self.assertIn('supported_real_connector', c.missing_settings())

    def test_deny_public_listener_and_invalid_url(self):
        for host in ('0.0.0.0', '192.168.1.2', '::', 'example.org'):
            with self.subTest(host=host), self.assertRaises(ConfigurationError):
                DeploymentConfig.from_mapping({'gateway_host': host})
        for url in ('http://valid.example', 'https://localhost', 'https://127.0.0.1',
                    'https://user:pass@bridge.example', 'https://bridge.example/path'):
            with self.subTest(url=url), self.assertRaises(ConfigurationError):
                DeploymentConfig.from_mapping({'external_url': url})
        self.assertEqual(DeploymentConfig.from_mapping({'gateway_host': '::1'}).gateway_host, '::1')

    def test_closed_schema(self):
        for c in ({'token': 'abc'}, {'gateway_port': True}, {'gateway_port': 65536},
                  {'connector_mode': 'unknown'}, {'sqlite_path': 'relative'}, {'gateway_host': 1}):
            with self.subTest(c=c), self.assertRaises(ConfigurationError):
                DeploymentConfig.from_mapping(c)

    def test_file_perms_symlink_parent_dir(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            key = base/'key'
            key.write_text('test bytes')
            key.chmod(0o600)
            self.assertIsNone(secure_file(str(key)))
            key.chmod(0o644)
            self.assertEqual(secure_file(str(key)), 'INSECURE_PERMISSIONS')
            key.chmod(0o600)
            link = base/'link'
            link.symlink_to(key)
            self.assertEqual(secure_file(str(link)), 'INSECURE_SYMLINK')
            self.assertEqual(secure_file(str(base/'missing')), 'NOT_CONFIGURED')
            sub = base/'sub'
            sub.mkdir(mode=0o700)
            self.assertIsNone(private_dir(str(sub)))
            sub.chmod(0o755)
            self.assertEqual(private_dir(str(sub)), 'INSECURE_PERMISSIONS')

    def test_empty_or_directory_not_credential(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(secure_file(d), 'NOT_REGULAR_FILE')
            path = Path(d)/'empty'
            path.touch()
            self.assertEqual(secure_file(str(path)), 'EMPTY_FILE')
