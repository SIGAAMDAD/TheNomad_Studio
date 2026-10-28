"""A05 startup, single-owner fencing, audit and disabled privileges."""
import tempfile
import unittest
from pathlib import Path

from studio_v16.service import SQLiteService
from studio_v16.gateway.interfaces import Principal


class ServiceSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {
            'database': str(self.root / 'db.sqlite'),
            'audit_path': str(self.root / 'audit.jsonl'),
            'tls_cert': str(self.root / 'tls.crt'), 'tls_key': str(self.root / 'tls.key'),
            'tls_ca': str(self.root / 'ca.crt'),
            'client_fingerprints': {'a' * 64: 'worker_one'},
            'repository_remotes': {}, 'source_routes': [],
            'host': '127.0.0.1', 'port': 18555, 'monitor_principal': 'worker_one'
        }

    def test_sqlite_shadow_health_and_fail_closed(self):
        with SQLiteService(self.config) as svc:
            h = svc.health()
            self.assertEqual(h['mode'], 'SHADOW')
            self.assertEqual(h['schema'], 'NOMAD-SERVICE-HEALTH/2')
            self.assertEqual(h['database_integrity'], 'ok')
            self.assertFalse(h['external_effects_enabled'])
            self.assertGreaterEqual(h['schema_migration_count'], 1)
            denied = svc.gateway.evaluate({}, Principal('worker_one'))
            self.assertEqual(denied['decision'], 'DENY')
            self.assertTrue((self.root / 'audit.jsonl').read_text())

    def test_second_writer_is_denied(self):
        with SQLiteService(self.config):
            with self.assertRaises((BlockingIOError, OSError)):
                SQLiteService(self.config)

    def test_foreign_config_keys_rejected(self):
        self.config['owner_override'] = True
        with self.assertRaisesRegex(ValueError, 'strict service configuration'):
            SQLiteService(self.config)

    def test_public_listener_is_rejected(self):
        self.config['host'] = '0.0.0.0'
        with self.assertRaisesRegex(ValueError, 'loopback-only'):
            SQLiteService(self.config)


if __name__ == '__main__':
    unittest.main()
