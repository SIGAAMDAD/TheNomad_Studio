"""An authenticated J request sees immediately revoked A project grants."""
import hashlib
import io
import json
from wsgiref.util import setup_testing_defaults

import pytest
apsw = pytest.importorskip('apsw')

from studio.storage.workforce import WorkforceStorage
from studio.storage.grants import CurrentGrants
from studio.api import (ConfiguredOpaqueVerifier, DomainRegistry, FrozenContracts,
                        StudioOperatorService, StudioWSGI, VerifiedPrincipal)


def request(app, token, project):
    env = {}
    setup_testing_defaults(env)
    env.update(REQUEST_METHOD='GET', PATH_INFO=f'/v1/projects/{project}',
               QUERY_STRING='', CONTENT_LENGTH='0',
               HTTP_AUTHORIZATION='Bearer '+token, **{'wsgi.input':io.BytesIO()})
    status=[]
    payload=b''.join(app(env,lambda code,headers:status.append(code)))
    return int(status[0][:3]),json.loads(payload)


def test_current_grant_lookup_and_revocation(tmp_path):
    store=WorkforceStorage.open(tmp_path/'grants.sqlite', fresh=True)
    try:
        conn=store._connection
        conn.execute("INSERT INTO principals VALUES('user','configured','subject','human')")
        for project in ('p1','p2'):
            conn.execute("INSERT INTO projects(project_id,name,charter_digest,state) VALUES(?,?,?,?)",
                         (project,project,'digest','active'))
        conn.execute("INSERT INTO project_grants VALUES('p1','user','member')")
        grants=CurrentGrants(store)
        assert grants.granted_project_ids('user')==('p1',)
        token='test.'+'s'*48
        verifier=ConfiguredOpaqueVerifier({hashlib.sha256(token.encode()).hexdigest():
            VerifiedPrincipal('user','configured','subject','proof',False)})
        class Domain:
            def getProject(self, actor, project_id):
                return {'project_id':project_id,'name':'Project','revision':1}
        domains=DomainRegistry()
        domains.register_owner('M',Domain())
        app=StudioWSGI(StudioOperatorService(FrozenContracts('CONTRACTS'),verifier,
                                             grants,domains,None))
        assert request(app,token,'p1')[0]==200
        assert request(app,token,'p2')[0]==403
        conn.execute("INSERT INTO project_grants VALUES('p2','user','member')")
        assert request(app,token,'p2')[0]==200
        conn.execute("DELETE FROM project_grants WHERE project_id='p1'")
        assert request(app,token,'p1')[0]==403
        conn.execute("UPDATE projects SET state='archived' WHERE project_id='p2'")
        assert grants.granted_project_ids('user')==()
        assert request(app,token,'p2')[0]==403
    finally:
        store.close()


def test_grants_fail_closed_without_verified_writer(tmp_path):
    with pytest.raises(TypeError):
        CurrentGrants(object())
    store=WorkforceStorage.open(tmp_path/'empty.sqlite',fresh=True)
    try:
        assert CurrentGrants(store).granted_project_ids('no-such-principal')==()
        assert CurrentGrants(store).granted_project_ids('')==()
    finally:
        store.close()
