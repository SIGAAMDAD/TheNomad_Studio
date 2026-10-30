from __future__ import annotations

import pytest
from studio.app.errors import StudioError
from studio.app.projects import Actor, Project, ProjectService
from studio.app.lifecycle import StudioRuntime, Component

SHA='1'*64
OWNER=Actor('owner','cockpit',frozenset({'p1','p2'}),'proof',True)
OTHER=Actor('other','cockpit',frozenset({'p2'}),'proof',False)
BAD=Actor('browser','chat',frozenset({'p1','p2'}),'',True)


class Store:
    def __init__(self):
        self.projects={}
        self.requests={}

    def bootstrap_project(self,*,principal_id,request_id,request_body_sha256,name,charter_digest):
        key=(principal_id,request_id)
        if key in self.requests:
            prev_hash, pid=self.requests[key]
            if prev_hash!=request_body_sha256: raise StudioError('conflict','idempotency reused')
            return self.projects[pid]
        pid=f'p{len(self.projects)+1}'
        record=Project(pid,name,1,'draft')
        self.projects[pid]=record
        self.requests[key]=(request_body_sha256,pid)
        return record

    def list_granted_projects(self,*,principal_id,limit):
        if principal_id=='owner':
            return [self.projects[k] for k in sorted(self.projects)][:limit]
        return [self.projects[k] for k in sorted(self.projects) if k=='p2'][:limit]

    def get_project(self,*,project_id,principal_id):
        return self.projects.get(project_id)

    def configure_project(self,*,project_id,principal_id,request_id,request_body_sha256,config_digest,expected_revision):
        current=self.projects[project_id]
        if expected_revision!=current.revision:raise StudioError('stale_revision','CAS')
        update=Project(current.project_id,current.name,current.revision+1,'active')
        self.projects[project_id]=update
        return update


class Probe:
    def probe(self,*,project_id):
        return {'github_relay_auth':True,'dotnet_host':False}


def test_project_two_scopes_and_preflight():
    store=Store()
    service=ProjectService(store,Probe())
    p1=service.createProject(OWNER,{'request_id':'r1','name':'A','charter_digest':SHA})['project_id']
    p2=service.createProject(OWNER,{'request_id':'r2','name':'B','charter_digest':SHA})['project_id']
    assert (p1,p2)==('p1','p2')
    assert len(service.listProjects(OWNER)['items'])==2
    assert [x['project_id'] for x in service.listProjects(OTHER)['items']]==['p2']
    with pytest.raises(StudioError) as e: service.getProject(OTHER,'p1')
    assert e.value.code=='forbidden'
    assert service.getProject(OTHER,'p2')['name']=='B'
    assert service.preflightProject(OTHER,'p2')['missing_capabilities']==['dotnet_host']


def test_create_bootstrap_idempotency_conflict():
    store=Store();service=ProjectService(store,Probe())
    request={'request_id':'repeat','name':'A','charter_digest':SHA}
    assert service.createProject(OWNER,request)==service.createProject(OWNER,request)
    with pytest.raises(StudioError) as e:
        service.createProject(OWNER,{**request,'name':'B'})
    assert e.value.code=='conflict'


def test_configure_project_cas_and_no_browser_owner():
    store=Store();service=ProjectService(store,Probe())
    service.createProject(OWNER,{'request_id':'r1','name':'A','charter_digest':SHA})
    assert service.configureProject(OWNER,'p1',{'request_id':'x','config_digest':SHA,'expected_revision':1})['revision']==2
    with pytest.raises(StudioError) as e:
        service.configureProject(OWNER,'p1',{'request_id':'y','config_digest':SHA,'expected_revision':1})
    assert e.value.code=='stale_revision'
    with pytest.raises(StudioError):
        service.createProject(BAD,{'request_id':'x','name':'A','charter_digest':SHA})
    with pytest.raises(StudioError) as e:
        service.createProject(OTHER,{'request_id':'x','name':'A','charter_digest':SHA})
    assert e.value.code=='requires_owner'


def test_recovery_all_hooks_before_start_and_reverse_stop():
    log=[]
    components=[Component(str(i),lambda i=i:log.append(f'r{i}'),
                          lambda i=i:log.append(f's{i}'),
                          lambda i=i:log.append(f'x{i}')) for i in range(3)]
    runtime=StudioRuntime(components)
    with runtime:
        assert runtime.ready
    assert log==['r0','r1','r2','s0','s1','s2','x2','x1','x0']
    assert not runtime.ready


def test_recovery_failure_starts_no_services():
    log=[]
    def fail(): raise RuntimeError('bad recovery')
    r=StudioRuntime([Component('db',lambda:log.append('recover-db'),lambda:log.append('start-db'),lambda:log.append('stop-db')),
                     Component('relay',fail,lambda:log.append('start-relay'),lambda:log.append('stop-relay'))])
    with pytest.raises(RuntimeError):r.start()
    assert log==['recover-db']
    assert not r.ready


def test_partial_start_rolls_back_in_reverse_order():
    log=[]
    def fail():raise RuntimeError('cannot start')
    r=StudioRuntime([Component('db',lambda:None,lambda:log.append('start-db'),lambda:log.append('stop-db')),
                     Component('bad',lambda:None,fail,lambda:log.append('stop-bad'))])
    with pytest.raises(RuntimeError):r.start()
    assert log==['start-db','stop-db']
