"""Project bootstrap and configuration through the real APSW M/C writer."""
import hashlib
import io
import json
from wsgiref.util import setup_testing_defaults

import pytest
apsw=pytest.importorskip('apsw')

from studio.api import (ConfiguredOpaqueVerifier, DomainRegistry, FrozenContracts,
                        StudioOperatorService, StudioWSGI, VerifiedPrincipal)
from studio.app.composition import StudioComposition
from studio.app.projects import ProjectService
from studio.storage.projects import ProjectAPSWStore
from studio.storage.grants import CurrentGrants
from studio.storage.workforce import WorkforceStorage


def _request(app, token, method, path, body=None):
    env={}
    setup_testing_defaults(env)
    raw=json.dumps(body).encode() if body is not None else b''
    env.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING='',
               CONTENT_LENGTH=str(len(raw)), CONTENT_TYPE='application/json',
               HTTP_AUTHORIZATION='Bearer '+token, **{'wsgi.input':io.BytesIO(raw)})
    statuses=[]
    response=b''.join(app(env,lambda status,headers:statuses.append(status)))
    return int(statuses[0][:3]),json.loads(response)


def test_http_project_bootstrap_configure_and_exact_replay(tmp_path):
    db=WorkforceStorage.open(tmp_path/'bootstrap.sqlite',fresh=True)
    try:
        db._connection.execute("INSERT INTO principals VALUES('owner','configured','human','human')")
        projects=ProjectAPSWStore(db)
        class Capabilities:
            def probe(self, *, project_id):
                return {'github':False,'host':True}
        m=StudioComposition(projects=ProjectService(projects,Capabilities()),
                            releases=object(),recovery=object(),runtime=object())
        registry=DomainRegistry()
        registry.register_owner('M',m)
        token='owner.'+'x'*48
        verifier=ConfiguredOpaqueVerifier({
            hashlib.sha256(token.encode()).hexdigest():
                VerifiedPrincipal('owner','configured','human','trusted-proof',True)})
        app=StudioWSGI(StudioOperatorService(FrozenContracts('CONTRACTS'),verifier,
                                             CurrentGrants(db),registry,None))
        create={'request_id':'bootstrap-1','name':'Nomad','charter_digest':'a'*64}
        code, response=_request(app,token,'POST','/v1/projects',create)
        assert code==200
        project=response['project_id']
        assert response['revision']==1
        assert _request(app,token,'POST','/v1/projects',create)==(code,response)
        assert _request(app,token,'POST','/v1/projects',{**create,'name':'Changed'})[0]==409
        assert _request(app,token,'GET',f'/v1/projects/{project}')[1]['name']=='Nomad'
        assert [p['project_id'] for p in _request(app,token,'GET','/v1/projects')[1]['items']]==[project]
        assert _request(app,token,'GET',f'/v1/projects/{project}/preflight')[1]['ready'] is False
        config={'request_id':'config-1','config_digest':'b'*64,'expected_revision':1}
        assert _request(app,token,'PUT',f'/v1/projects/{project}/config',config)==(
            200,{'project_id':project,'revision':2})
        assert _request(app,token,'PUT',f'/v1/projects/{project}/config',config)==(
            200,{'project_id':project,'revision':2})
        code, response=_request(app,token,'PUT',f'/v1/projects/{project}/config',
                                 {**config,'request_id':'stale'})
        assert code==409 and response['code']=='stale_revision'
        rows=list(db._connection.execute(
            'SELECT event_type,event_seq FROM audit_events WHERE project_id=? ORDER BY event_seq',
            (project,)))
        assert rows==[('project.created',1),('project.configured',2)]
        assert db._connection.execute(
            'SELECT config_digest FROM project_profiles WHERE project_id=? AND profile_id=?',
            (project,'active')).fetchone()==('b'*64,)
    finally:
        db.close()


def test_unprovisioned_actor_cannot_create_project(tmp_path):
    writer=WorkforceStorage.open(tmp_path/'denied.sqlite',fresh=True)
    try:
        with pytest.raises(Exception,match='verified human'):
            ProjectAPSWStore(writer).bootstrap_project(
                principal_id='missing',request_id='id',request_body_sha256='a'*64,
                name='unapproved',charter_digest='b'*64)
        assert writer._connection.execute('SELECT COUNT(*) FROM projects').fetchone()==(0,)
    finally:
        writer.close()
