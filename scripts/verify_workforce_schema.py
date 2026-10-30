"""Check that the runtime schema contains enforceable worker state contracts."""
from __future__ import annotations

from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DDL = ROOT / 'schema' / 'initial_schema.sql'


def check() -> None:
    con = sqlite3.connect(':memory:')
    try:
        con.executescript(DDL.read_text())
        tables = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'worker_conversations', 'worker_shifts', 'shift_assignments',
                    'command_outbox', 'command_receipts', 'workforce_command_results'}
        missing = required - tables
        if missing:
            raise RuntimeError(f'missing required worker state tables: {sorted(missing)}')
        foreign_keys = con.execute('PRAGMA foreign_key_check').fetchall()
        if foreign_keys:
            raise RuntimeError(f'schema foreign-key violations: {foreign_keys}')
        # A trigger is a behavior contract: a successful DDL parse cannot prove it.
        con.execute("INSERT INTO projects(project_id,name,charter_digest,state) VALUES('p','p','digest','active')")
        con.execute("INSERT INTO idempotency(project_id,action_owner,request_id,payload_digest,status,result_digest) VALUES('p','workers.test','r',?,'finished',?)", ('a'*64,'b'*64))
        con.execute("INSERT INTO workforce_command_results(project_id,action_owner,request_id,result_json) VALUES('p','workers.test','r','{}')")
        for mutation in (
            "DELETE FROM workforce_command_results WHERE project_id='p'",
            "UPDATE workforce_command_results SET result_json='[]' WHERE project_id='p'",
            "UPDATE idempotency SET result_digest='corrupt' WHERE project_id='p'",
        ):
            try:
                con.execute(mutation)
            except sqlite3.IntegrityError:
                pass
            else:
                raise RuntimeError(f'durable result evidence mutable: {mutation}')
    finally:
        con.close()


if __name__ == '__main__':
    check()
    print('workforce schema verification passed')
