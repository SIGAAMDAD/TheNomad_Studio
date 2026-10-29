"""Local-only fixtures. These signatures are NEVER production host evidence."""
from __future__ import annotations
import hashlib
import hmac
import sqlite3
from pathlib import Path
from nomad_studio.trust.evidence import TrustAnchor, ProviderEvidenceVerifier
from nomad_studio.trust.port_types import canonical

KEY=b'local_fixture_not_a_production_provider_secret_key'

class SignedNative:
    def __init__(self, production=False):
        self.receipts={}
        self.anchor=TrustAnchor('fixture/issuer','fixture/provider',frozenset({
            'OWNER_COMMAND','OWNER_DECISION','WORKER_SESSION','PROVIDER_PERMISSION',
            'HOST_MANIFEST','HOST_SESSION','HOST_ACTIVATION','HOST_IDLE','HOST_DISPATCH',
            'HOST_CHECKPOINT','HOST_FENCE','HOST_CANCEL','OVERSIGHT_NOTICE'}),KEY,production)
        self.verifier=ProviderEvidenceVerifier(self.read, {'fixture/issuer':self.anchor})
    def sign(self,ref,purpose, **claims):
        body={'issuer_id':'fixture/issuer','purpose':purpose,'claims':dict(
            provider_id='fixture/provider',simulated=False,native_ref=ref,**claims)}
        body['signature']=hmac.new(KEY,canonical(body),hashlib.sha256).hexdigest()
        self.receipts[ref]=body
        return ref
    def read(self,ref): return self.receipts.get(ref)

class Store:
    def __init__(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        schema=Path(__file__).with_name('fixtures')/'initial_schema_v1.sql'
        self.db.executescript(schema.read_text())
    @staticmethod
    def unpack(row): return dict(row) if row else None
    def read_one(self,table,where,params):
        if table not in {'v1_verified_identities','v1_host_sessions','v1_provider_capabilities',
                         'v1_host_activations','v1_trusted_host_observations'}: raise AssertionError('foreign table')
        return self.unpack(self.db.execute(f'SELECT * FROM {table} WHERE {where}',params).fetchone())
    def transaction(self, fn):
        class Tx:
            def __init__(self,db):self.db=db
            def fetch_one(self,sql,params=()):return Store.unpack(self.db.execute(sql,params).fetchone())
            def execute(self,sql,params=()):return self.db.execute(sql,params)
        with self.db:
            return fn(Tx(self.db))
    def enroll(self,principal='owner/1',credential='credential/1',epoch=4,expires=10_000_000):
        self.db.execute('INSERT INTO v1_verified_identities VALUES (?,?,?,?,?,?)',
                        (principal,credential,'fixture/issuer',epoch,expires,'enrollment/1'))
        self.db.commit()

class Journal:
    def __init__(self):self.entries={};self.unknown=[];self.verified=[]
    def reserve(self,kind,key,digest):
        lookup=(kind,key)
        if lookup not in self.entries:
            self.entries[lookup]=digest;return 'NEW'
        return 'SAME' if self.entries[lookup]==digest else 'CONFLICT'
    def mark_unknown(self,kind,key,digest):self.unknown.append((kind,key,digest))
    def mark_verified(self,kind,key,digest,ref):self.verified.append((kind,key,digest,ref))
