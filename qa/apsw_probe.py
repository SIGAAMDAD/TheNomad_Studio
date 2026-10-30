"""Optional actual APSW reference-schema probe. Never labelled production service proof."""
from __future__ import annotations
from pathlib import Path


def run(schema: Path) -> dict:
    try:
        import apsw
    except ImportError as exc:
        raise FileNotFoundError('APSW missing; no real APSW SQL run') from exc
    conn = apsw.Connection(':memory:')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute(schema.read_text(encoding='utf-8'))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    if len(tables) != 66:
        raise AssertionError(f'wrong reference schema table count: {len(tables)}')
    evidence = []
    def insert(sql, params=()):
        conn.execute(sql, params)
    def denied(label, sql, params=()):
        conn.execute('SAVEPOINT neg')
        try:
            try:
                conn.execute(sql, params)
            except (apsw.ConstraintError, apsw.SQLError) as exc:
                evidence.append({'case':label, 'denied':True, 'exception_type':type(exc).__name__})
                return
            raise AssertionError(f'negative SQL operation accepted: {label}')
        finally:
            conn.execute('ROLLBACK TO neg');conn.execute('RELEASE neg')
    insert('INSERT INTO principals VALUES (?,?,?,?)',('owner','issuer','owner','human'))
    insert('INSERT INTO projects(project_id,name,charter_digest,state) VALUES (?,?,?,?)',('p1','Alpha','ha','active'))
    insert('INSERT INTO projects(project_id,name,charter_digest,state) VALUES (?,?,?,?)',('p2','Beta','hb','active'))
    insert('INSERT INTO cabals VALUES (?,?,?,?,?)',('p1','cab-a','Cabal','sha','active'))
    insert('INSERT INTO cabals VALUES (?,?,?,?,?)',('p2','cab-b','Cabal','sha','active'))
    denied('duplicate project primary key', 'INSERT INTO projects(project_id,name,charter_digest,state) VALUES (?,?,?,?)',('p1','Other','x','active'))
    denied('unauthorized cross-project grant', 'INSERT INTO project_grants VALUES (?,?,?)', ('p3','owner','owner'))
    denied('wrong owner decision proposal',
           'INSERT INTO owner_decisions(project_id,decision_id,subject_type,subject_id,action,effect_digest,verified_owner_principal_id,proof_digest,state) VALUES (?,?,?,?,?,?,?,?,?)',
           ('p1','faked','project','p1','project.configure','eff','owner','proof','approved'))
    denied('nonexistent task dependency',
           'INSERT INTO task_dependencies(project_id,task_id,depends_on_task_id) VALUES (?,?,?)',('p1','missing','other'))
    insert('INSERT INTO idempotency(project_id,action_owner,request_id,payload_digest,status) VALUES (?,?,?,?,?)',
           ('p1','A','request','digest-A','finished'))
    denied('duplicate idempotency with different payload',
           'INSERT INTO idempotency(project_id,action_owner,request_id,payload_digest,status) VALUES (?,?,?,?,?)',
           ('p1','A','request','digest-B','finished'))
    denied('negative budget amount', 'INSERT INTO budgets(project_id,budget_id,resource_key,limit_amount) VALUES (?,?,?,?)',
           ('p1','budget','cpu',-1))
    return {'apsw_version':apsw.apswversion(), 'sqlite_version':apsw.sqlitelibversion(),
            'table_count':len(tables),'negative_cases':evidence}
