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
        required = {'worker_conversations', 'worker_shifts', 'worker_lineages', 'shift_assignments',
                    'command_outbox', 'command_receipts', 'workforce_command_results'}
        missing = required - tables
        if missing:
            raise RuntimeError(f'missing required worker state tables: {sorted(missing)}')
        obsolete = {'sessions', 'session_lineages'}
        if obsolete & tables:
            raise RuntimeError('obsolete task-bound session persistence survived')
        # Verify the persisted shift identity is the foreign-key target for QA,
        # submissions, relay receipts and artifact provenance.
        for table, column, target in (
            ('submissions', 'shift_id', 'worker_shifts'),
            ('qa_reviews', 'author_shift_id', 'worker_shifts'),
            ('artifacts', 'producer_shift_id', 'worker_shifts'),
            ('relay_receipts', 'recipient_shift_id', 'worker_shifts'),
            ('worker_lineages', 'root_worker_id', 'worker_conversations'),
        ):
            fk = list(con.execute(f'PRAGMA foreign_key_list("{table}")'))
            columns = {c[3] for c in fk if c[2] == target}
            if column not in columns:
                raise RuntimeError(f'{table}.{column} lacks its {target} FK')
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
