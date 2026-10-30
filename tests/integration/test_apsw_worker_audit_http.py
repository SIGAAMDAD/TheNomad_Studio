"""APSW-backed WSGI integration of real I worker and J audit services.

This test does not substitute a browser or GitHub provider. It exercises a
single physical SQLite database through independent HTTP domain handlers.
"""
from __future__ import annotations

import hashlib
import io
import json
from wsgiref.util import setup_testing_defaults

import pytest

apsw = pytest.importorskip('apsw')

from studio.agents.operations import WorkerOperations
from studio.api import (ConfiguredOpaqueVerifier, DomainRegistry, EventPageService,
                        FrozenContracts, StudioOperatorService, StudioWSGI, VerifiedPrincipal)
from studio.storage.audit import ProjectAuditReader
from studio.storage.engine import ScopedStore
from studio.storage.workforce import WorkforceStorage
from studio.workforce import Workforce


def _invoke(app, token, method, path, body=None, query=''):
    environ = {}
    setup_testing_defaults(environ)
    raw = json.dumps(body, separators=(',', ':')).encode() if body is not None else b''
    environ.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING=query,
                   CONTENT_LENGTH=str(len(raw)), CONTENT_TYPE='application/json',
                   HTTP_AUTHORIZATION='Bearer '+token, **{'wsgi.input':io.BytesIO(raw)})
    statuses = []
    result = b''.join(app(environ,lambda status,headers:statuses.append(status)))
    return int(statuses[0][:3]),json.loads(result)


def test_apsw_worker_and_audit_share_durable_state_under_http(tmp_path):
    path=tmp_path/'studio.sqlite'
    workers_db=WorkforceStorage.open(path,fresh=True)
    audit_db=None
    try:
        conn=workers_db._connection
        conn.execute("INSERT INTO principals VALUES('owner','trust','owner','human')")
        conn.execute("INSERT INTO projects(project_id,name,charter_digest,state) "
                     "VALUES('p','studio','digest','active')")
        conn.execute("INSERT INTO specialist_roles(role_id,role_revision,role_manifest_digest,state) "
                     "VALUES('coder','1','digest','ready')")
        conn.execute("INSERT INTO audit_events(project_id,event_seq,event_type,subject_id,payload_digest,actor_principal_id) "
                     "VALUES('p',1,'project.created','p',?,'owner')",('d'*64,))
        audit_db=ScopedStore(path)
        token='owner.'+'a'*48
        verifier=ConfiguredOpaqueVerifier({
            hashlib.sha256(token.encode()).hexdigest():
                VerifiedPrincipal('owner','trusted','owner','proof',True)})
        class Grants:
            def granted_project_ids(self,principal_id):
                # Re-read durable grants on every HTTP request.
                return ['p'] if principal_id=='owner' else []

        def authorized(actor, project_id, operation):
            if actor['principal_id']!='owner' or project_id!='p':
                raise ValueError('not authorized')

        worker_ops=WorkerOperations(Workforce(workers_db,require_action=authorized),
                                    verify_receipt=lambda *_:True)
        registry=DomainRegistry()
        registry.register_owner('I',worker_ops)
        registry.register_owner('J',EventPageService(ProjectAuditReader(audit_db)))
        app=StudioWSGI(StudioOperatorService(FrozenContracts('CONTRACTS'),verifier,
                                             Grants(),registry,None))
        enrollment={'request_id':'reg-1','worker_id':'worker-1',
                    'browser_chat_ref':'manual-browser-conversation','role_id':'coder'}
        code,result=_invoke(app,token,'POST','/v1/projects/p/workers',enrollment)
        assert (code,result)==(200,{'worker_id':'worker-1','state':'registered'})
        assert _invoke(app,token,'POST','/v1/projects/p/workers',enrollment)==(code,result)
        code,result=_invoke(app,token,'POST','/v1/projects/p/workers/worker-1/shifts',
                            {'request_id':'begin-1','shift_id':'s1'})
        assert code==200 and result['state']=='awaiting_hello'
        code,result=_invoke(app,token,'GET','/v1/projects/p/workers')
        assert code==200 and result['items'][0]['worker_id']=='worker-1'
        code,result=_invoke(app,token,'GET','/v1/projects/p/events',query='after=0&limit=100')
        assert code==200 and result['next_cursor']==1
        assert result['events'][0]['event_type']=='project.created'
        assert workers_db.read('p',lambda tx:tx.one(
            'SELECT state FROM worker_shifts WHERE project_id=? AND shift_id=?',
            ('p','s1')))['state']=='awaiting_hello'
        # A refused project crosses neither I nor J even with the same bearer.
        assert _invoke(app,token,'GET','/v1/projects/other/events')[0]==403
        assert _invoke(app,token,'GET','/v1/projects/other/workers')[0]==403
        assert _invoke(app,token,'POST','/v1/projects/p/workers',
                       {**enrollment,'role_id':'wrong'})[0]==409
    finally:
        if audit_db is not None:
            audit_db.close()
        workers_db.close()
