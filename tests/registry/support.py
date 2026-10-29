"""CAB-G test-only SQLite driver. Never production host/provider evidence."""
from __future__ import annotations

import pathlib
import sqlite3

SHA='a'*64
CONTRACT='b'*64
BUDGET='c'*64

class SQLiteTestDriver:
    def __init__(self):
        self.conn=sqlite3.connect(':memory:',isolation_level=None)
        self.conn.row_factory=sqlite3.Row
        schema=(pathlib.Path(__file__).parent/'fixtures'/'frozen_initial_schema.sql').read_text()
        self.conn.executescript(schema)
        self.conn.execute('PRAGMA foreign_keys=ON')

    def read(self, fn):
        return fn(self.conn)

    def write(self, fn):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            result=fn(self.conn)
            self.conn.commit()
            return result
        except Exception:
            self.conn.rollback()
            raise

    def seed(self):
        self.conn.execute('INSERT INTO v1_projects VALUES(?,?,?,?,?)',('proj','owner','f'*64,'ACTIVE',1))
        self.conn.execute('INSERT INTO v1_briefs VALUES(?,?,?,?,?,?,?,?)',('proj','brief','feature','owner','brief','1'*64,'cmd',1))
        self.conn.execute('INSERT INTO v1_feature_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
             ('proj','feature',1,'brief','1'*64,SHA,CONTRACT,'e'*64,'d'*64,'7'*64,10,'{}'))
        self.conn.execute('INSERT INTO v1_feature_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
             ('proj','feature','brief',CONTRACT,SHA,'ACTIVE',1,1,None,2))
        self.conn.execute('INSERT INTO v1_cabals VALUES(?,?,?,?,?,?)',('proj','cabal',1,SHA,SHA,'ACTIVE'))
        self.conn.execute('INSERT INTO v1_cabal_capacity VALUES(?,?,?,?,?)',('proj','cabal','task',2,0))
