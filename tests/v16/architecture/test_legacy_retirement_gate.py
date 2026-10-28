"""R07 negative source and workflow compatibility detection."""
import tempfile
from pathlib import Path
import unittest
from scripts.legacy_retirement_gate import inspect_files

class RetirementGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def create(self,path,value):
        file=self.root/path
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text(value)
        return path
    def test_postgres_dependency_denied(self):
        path=self.create('studio_v16/db/sqlite/unsafe.py','from studio_v16.db.store import PostgresStore\n')
        codes={x['code'] for x in inspect_files(self.root,[path])}
        self.assertIn('RETIRED_MODULE_IMPORT',codes)
        self.assertIn('LEGACY_RUNTIME_REFERENCE',codes)
    def test_writable_branch_workflow_denied(self):
        path=self.create('.github/workflows/studio-reconcile.yml',
                         'on: schedule\nrun: git push origin studio/runtime\n')
        codes={x['code'] for x in inspect_files(self.root,[path])}
        self.assertIn('LEGACY_PATH_PRESENT',codes)
        self.assertIn('LEGACY_RUNTIME_REFERENCE',codes)
    def test_old_prompt_even_unreferenced_denied(self):
        path=self.create('studio/v1/prompts/role.md','# historical only\n')
        self.assertIn('LEGACY_PATH_PRESENT',{x['code'] for x in inspect_files(self.root,[path])})
    def test_current_sqlite_clean(self):
        path=self.create('studio_v16/db/sqlite/new.py','import sqlite3\n')
        self.assertEqual(inspect_files(self.root,[path]),[])
