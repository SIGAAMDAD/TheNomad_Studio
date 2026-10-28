#!/usr/bin/env python3
"""R02: SQLite-only read-only local operator report. Never reads V1 Git state."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote

@contextmanager
def operator_reader(database:Path):
    path=Path(database)
    if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('absolute non-symlink SQLite path required')
    if not path.is_file() or path.stat().st_uid!=os.getuid():
        raise ValueError('service-owned database required')
    info=path.parent.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077:
        raise ValueError('private service directory required')
    if sqlite3.sqlite_version_info<(3,45,0):
        raise ValueError('SQLite >=3.45 required')
    uri='file:'+quote(str(path),safe='/')+'?mode=ro'
    with sqlite3.connect(uri,uri=True,isolation_level=None) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN')
        try:
            yield conn
        finally:
            conn.rollback()

def snapshot(db,*,department=None,limit=100):
    if type(limit) is not int or not 1<=limit<=500:
        raise ValueError('bounded positive page size required')
    if department is not None and (not isinstance(department,str) or not 1<=len(department)<=64):
        raise ValueError('invalid department')
    mode=db.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone()
    if mode is None or mode[0] not in ('SHADOW','ACTIVE'):
        raise ValueError('unsupported SQLite authority mode')
    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
        raise ValueError('database integrity degraded')
    condition='WHERE d.department=?' if department is not None else ''
    parameters=(department,limit) if department is not None else (limit,)
    jobs=db.execute('''SELECT j.job_id,j.state,j.source_repo,j.source_base_sha,j.assignment_epoch,
        d.department FROM jobs j LEFT JOIN job_departments d ON d.job_id=j.job_id '''+
        condition+' ORDER BY j.created_at,j.job_id LIMIT ?',parameters).fetchall()
    condition='WHERE department=?' if department is not None else ''
    responses=db.execute('''SELECT response_id,status,report_json IS NOT NULL FROM response_attempts '''+
                         condition+' ORDER BY started_at DESC,response_id LIMIT ?',parameters).fetchall()
    return dict(schema='NOMAD-SQLITE-OPERATOR-REPORT/1',authority_mode=mode[0],
                observed_from='SQLITE_READONLY',limit=limit,department=department,
                jobs=[dict(job_id=r[0],state=r[1],source_repo=r[2],source_sha=r[3],
                           epoch=r[4],department=r[5]) for r in jobs],
                responses=[dict(response_id=r[0],runtime_status=r[1],
                                report='OBSERVED' if r[2] else 'UNKNOWN') for r in responses],
                governance_approval='NOT_ASSERTED',offhost_monitor='NOT_ASSERTED')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--department')
    parser.add_argument('--limit',type=int,default=100)
    args=parser.parse_args()
    with operator_reader(args.database) as db:
        result=snapshot(db,department=args.department,limit=args.limit)
    print(json.dumps(result,sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
