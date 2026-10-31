"""Local contract acceptance. Live GitHub/browser acceptance is a separate gate."""
from pathlib import Path
import hashlib
import json
import sqlite3

from studio.api.contracts import FrozenContracts

ROOT=Path(__file__).resolve().parents[2]


def test_all_declared_api_operations_resolve():
    contracts=FrozenContracts(ROOT/'CONTRACTS')
    assert len(contracts.operations)==72
    assert contracts.operations['dispatchTask'].response_schema['required']==[
        'task_id','lease_generation','command_id','shift_id','delivery_state']


def test_initial_database_has_shift_distinct_from_assignment():
    db=sqlite3.connect(':memory:')
    db.executescript((ROOT/'schema/initial_schema.sql').read_text())
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'worker_conversations','worker_shifts','shift_assignments','command_outbox','command_receipts'}<=names
    index={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert 'one_open_shift_per_worker' in index
    db.close()


def test_pinned_sql_matches_owned_sql():
    sql=(ROOT/'schema/initial_schema.sql').read_bytes()
    assert (ROOT/'CONTRACTS/initial_schema.sql').read_bytes()==sql
    assert (ROOT/'schema/schema.sha256').read_text().split()[0]==hashlib.sha256(sql).hexdigest()


def test_student_capacity_is_an_optional_profile():
    from studio.operations.capacity import select_profile, Profile
    assert select_profile({}) is Profile.FREE_BASELINE
    assert select_profile({'STUDIO_CAPACITY_PROFILE':'STUDENT_ENHANCED'}) is Profile.STUDENT_ENHANCED
