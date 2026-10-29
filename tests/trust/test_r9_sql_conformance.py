"""Frozen R9 SQL/ownership conformance; local synthetic fixture, not P2 state QA."""
import hashlib
import json
import sqlite3
from pathlib import Path
import pytest

FIXTURE=Path(__file__).parent/'fixtures'
H_TABLES={'v1_verified_identities','v1_host_sessions','v1_host_activations',
          'v1_provider_capabilities','v1_trusted_host_observations'}

def database():
    db=sqlite3.connect(':memory:')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript((FIXTURE/'initial_schema_v1.sql').read_text())
    return db

def test_all_56_physical_tables_and_exact_five_owned_tables():
    db=database()
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'v1_%'")}
    assert len(tables)==56
    owners=json.loads((FIXTURE/'schema_table_owners.json').read_text())['owner_per_table']
    assert set(owners)==tables
    assert {name for name,owner in owners.items() if owner=='CAB-H'}==H_TABLES
    assert hashlib.sha256((FIXTURE/'initial_schema_v1.sql').read_bytes()).hexdigest()==(
        'f2a76f51fef851fe972500c906f31cde84bf63e446a4a149ab6ac08c9fe410f5')

def test_foreign_keys_and_epoch_constraints():
    db=database()
    for table in ('v1_host_activations','v1_trusted_host_observations'):
        assert any(row[2]=='v1_host_sessions' for row in db.execute(f'PRAGMA foreign_key_list({table})'))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO v1_host_activations VALUES ('missing','job/1','native/ref',100)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO v1_verified_identities VALUES ('worker','cred','issuer',-1,100,'ref')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO v1_provider_capabilities VALUES ('provider','dispatch','FAKE_SUPPORTED','ref')")


def test_host_activation_key_replay_and_unique_receipts():
    db=database()
    db.execute("INSERT INTO v1_host_sessions VALUES ('s','p','w','digest',1,2)")
    db.execute("INSERT INTO v1_host_activations VALUES ('s','job/1','native/first',100)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO v1_host_activations VALUES ('s','job/2','native/first',200)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO v1_host_activations VALUES ('s','job/1','native/other',200)")
