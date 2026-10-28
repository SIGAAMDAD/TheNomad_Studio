"""A09 external monitor cannot hide stale, unsafe or corrupt authority state."""
from datetime import datetime,timedelta,timezone
import unittest
from infra.sqlite.external_monitor import evaluate,Degraded

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.sample={'schema':'NOMAD-SERVICE-HEALTH/2','mode':'SHADOW',
                     'database_integrity':'ok','schema_migration_count':4,
                     'external_effects_enabled':False,'journal_mode':'wal',
                     'synchronous':2,'observed_at':self.now.isoformat().replace('+00:00','Z')}
    def test_healthy_shadow(self):
        self.assertEqual(evaluate(self.sample,now=self.now)['status'],'HEALTHY')
    def test_unapproved_write_and_mode_denied(self):
        self.sample['external_effects_enabled']=True
        with self.assertRaises(Degraded): evaluate(self.sample,now=self.now)
        self.sample['mode']='ACTIVE'
        with self.assertRaises(Degraded): evaluate(self.sample,now=self.now)
    def test_stale_or_future_sample_denied(self):
        self.sample['observed_at']=(self.now-timedelta(minutes=2)).isoformat().replace('+00:00','Z')
        with self.assertRaises(Degraded): evaluate(self.sample,now=self.now)
    def test_integrity_or_full_sync_denied(self):
        self.sample['database_integrity']='corrupt'
        with self.assertRaises(Degraded): evaluate(self.sample,now=self.now)
        self.sample['database_integrity']='ok'
        self.sample['synchronous']=1
        with self.assertRaises(Degraded): evaluate(self.sample,now=self.now)
