"""Project-scoped WG-B commands matching frozen operator operation shapes.

No SQL driver, model API, role-claim-based authorization, or relay publishing here.
WG-C owns atomic APSW persistence/idempotency; WG-A/J own authenticated policy;
WG-I/G own GitHub relay delivery. This module does not infer their success.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Mapping, TypeVar
from uuid import uuid4
from .ports import (OrganizationStore, OrganizationTransaction, PermissionPolicy,
                    TrustVerifier, ContentSource, HostReadiness)
from .verification import (OrganizationError, canonical_digest, digest,
                           load_json, require_digest, require_id,
                           role_readiness, package_manifest, verify_blob)

T = TypeVar('T')

@dataclass(frozen=True)
class TrustedActor:
    """Must be instantiated from verified WG-J authentication, not caller JSON."""
    principal_id: str
    allowed_projects: frozenset[str]
    owner_projects: frozenset[str]
    proof_ref: str

    def require(self, project_id: str, *, owner: bool = False) -> None:
        require_id(project_id)
        if (not self.proof_ref or not self.principal_id or
            project_id not in self.allowed_projects or
            (owner and project_id not in self.owner_projects)):
            raise OrganizationError('requires_owner' if owner else 'forbidden',
                                    'trusted project authority required')


class OrganizationService:
    def __init__(self, store: OrganizationStore, policy: PermissionPolicy,
                 *, source: ContentSource, trust: TrustVerifier,
                 host: HostReadiness | None = None):
        self.store, self.policy, self.source, self.trust, self.host = (
            store, policy, source, trust, host)

    def _read(self, actor: TrustedActor, project_id: str,
              action: Callable[[OrganizationTransaction], T], *, owner: bool = False) -> T:
        actor.require(project_id, owner=owner)
        return self.store.read(project_id, action)

    def _mutate(self, actor: TrustedActor, project_id: str, name: str,
                request_id: str, payload: Mapping[str, Any],
                action: Callable[[OrganizationTransaction], T], *, owner: bool = False) -> T:
        actor.require(project_id, owner=owner)
        require_id(request_id)
        return self.store.mutate(project_id=project_id, principal_id=actor.principal_id,
                                 action_name=name, request_id=request_id,
                                 payload_digest=canonical_digest({'operation_id':name,'payload':payload}), action=action)

    def listPacks(self, actor: TrustedActor, project_id: str) -> dict:
        def read(tx):
            installations = {r['package_digest']:r for r in tx.many('project_packages', project_id=project_id)}
            return {'packages': [
                {'package_digest':p['package_digest'],'kind':p['kind'],
                 'enabled':bool(installations[p['package_digest']]['enabled'])}
                for p in tx.many('package_revisions') if p['package_digest'] in installations]}
        return self._read(actor, project_id, read)

    def register_package(self, actor: TrustedActor, project_id: str, request_id: str,
                         raw_manifest: bytes, signature_ref: str) -> dict:
        """Internal verified installation command, intentionally not a public HTTP op."""
        actor.require(project_id, owner=True)
        manifest = package_manifest(raw_manifest, self.source)
        if not isinstance(signature_ref,str) or not signature_ref:
            raise OrganizationError('invalid','detached signature reference required')
        self.trust.verify_package(manifest=raw_manifest, signature_ref=signature_ref)
        pd = digest(raw_manifest)
        def write(tx):
            existing = tx.one('package_revisions', package_digest=pd)
            if existing is not None:
                return {'package_digest':pd, 'installed':True}
            for prior in tx.many('package_revisions'):
                if (prior['package_id']==manifest['package_id'] and
                    prior['package_version']==manifest['package_version'] and
                    prior['package_digest']!=pd):
                    raise OrganizationError('conflict','package version already pins different bytes')
            for dep in manifest.get('dependencies', []):
                existing_dep = tx.one('package_revisions', package_digest=dep)
                if existing_dep is None or existing_dep['state'] != 'installed':
                    raise OrganizationError('capability_unavailable', 'dependency not installed: '+dep)
            tx.insert('package_revisions', {'package_digest':pd,
                'package_id':manifest['package_id'], 'package_version':manifest['package_version'],
                'kind':manifest['kind'],'manifest_digest':pd,'state':'installed'})
            for dep in manifest.get('dependencies', []):
                tx.insert('package_dependencies', {'package_digest':pd, 'required_digest':dep})
            for ent in manifest.get('entrypoints', []):
                tx.insert('tool_entrypoints', {'package_digest':pd,
                    'entrypoint_id':ent['id'],
                    'executable_digest':ent['executable_digest'],
                    'args_schema_digest':ent['args_schema_digest'],
                    'output_schema_digest':ent['output_schema_digest'],
                    'required_isolation':ent['required_isolation']})
            return {'package_digest':pd,'installed':True}
        return self._mutate(actor, project_id, 'B.register_package', request_id,
                            {'package_digest':pd}, write, owner=True)

    def enablePack(self, actor: TrustedActor, project_id: str, *, request_id: str,
                   package_digest: str, grants: list[dict[str, str]]) -> dict:
        require_digest(package_digest)
        if not isinstance(grants, list):
            raise OrganizationError('invalid','grants must be an array')
        normalized = []
        for grant in grants:
            if not isinstance(grant, dict) or set(grant) != {'package_digest','entrypoint_id','workspace_id','grant_digest'}:
                raise OrganizationError('invalid', 'invalid grant input')
            if grant['package_digest'] != package_digest:
                raise OrganizationError('forbidden', 'cross-package grant')
            normalized.append({'package_digest':package_digest,
                'entrypoint_id': require_id(grant['entrypoint_id']),
                'workspace_id': require_id(grant['workspace_id']),
                'grant_digest': require_digest(grant['grant_digest'])})
        if len({g['entrypoint_id'] for g in normalized}) != len(normalized):
            raise OrganizationError('conflict', 'duplicate tool grants')
        actor.require(project_id)
        # Approval is from trusted policy; caller-supplied digest grants no authority.
        self.policy.check_tool_grants(actor.principal_id, project_id, normalized)
        def write(tx):
            rev = tx.one('package_revisions',package_digest=package_digest)
            if rev is None or rev['state'] != 'installed':
                raise OrganizationError('capability_unavailable', 'verified package not installed')
            for dep in tx.many('package_dependencies', package_digest=package_digest):
                enabled = tx.one('project_packages', project_id=project_id,
                                 package_digest=dep['required_digest'])
                if not enabled or not enabled['enabled']:
                    raise OrganizationError('capability_unavailable', 'project dependency disabled')
            entrypoints = {r['entrypoint_id'] for r in tx.many('tool_entrypoints',package_digest=package_digest)}
            if entrypoints != {g['entrypoint_id'] for g in normalized}:
                raise OrganizationError('invalid','all package tool entrypoints need explicit grants')
            existing = tx.one('project_packages',project_id=project_id,package_digest=package_digest)
            config_digest = canonical_digest({'grants':normalized,'package_digest':package_digest})
            if existing:
                if existing['enabled']:
                    if existing['config_digest'] != config_digest:
                        raise OrganizationError('conflict','already enabled under a different configuration')
                    return {'package_digest':package_digest,'enabled':True}
                tx.change('project_packages',{'project_id':project_id,'package_digest':package_digest},
                          {'enabled':1,'config_digest':config_digest})
            else:
                tx.insert('project_packages',{'project_id':project_id,'package_digest':package_digest,
                                              'enabled':1,'config_digest':config_digest})
            for grant in normalized:
                old = [g for g in tx.many('tool_grants',project_id=project_id,
                                         package_digest=package_digest)
                       if g['entrypoint_id']==grant['entrypoint_id'] and not g['revoked']]
                if old:
                    raise OrganizationError('conflict','existing active tool grant; revoke explicitly')
                tx.insert('tool_grants',{'project_id':project_id,'grant_id':uuid4().hex,
                          **grant,'revoked':0})
            return {'package_digest':package_digest,'enabled':True}
        return self._mutate(actor,project_id,'B.enablePack',request_id,
                {'package_digest':package_digest,'grants':normalized},write)

    def disablePack(self, actor: TrustedActor, project_id: str, *, request_id: str,
                    package_digest: str) -> dict:
        require_digest(package_digest)
        def write(tx):
            current = tx.one('project_packages',project_id=project_id,package_digest=package_digest)
            if not current:
                raise OrganizationError('missing', 'project package not installed')
            for dependent in tx.many('package_dependencies', required_digest=package_digest):
                active = tx.one('project_packages',project_id=project_id,
                                package_digest=dependent['package_digest'])
                if active and active['enabled']:
                    raise OrganizationError('conflict','enabled dependent must be disabled first')
            tx.change('project_packages',{'project_id':project_id,'package_digest':package_digest},
                      {'enabled':0})
            for grant in tx.many('tool_grants',project_id=project_id,package_digest=package_digest):
                if not grant['revoked']:
                    tx.change('tool_grants',{'project_id':project_id,'grant_id':grant['grant_id']},
                              {'revoked':1})
            return {'package_digest':package_digest,'enabled':False}
        return self._mutate(actor,project_id,'B.disablePack',request_id,
                            {'package_digest':package_digest},write)

    def createCabal(self, actor: TrustedActor, project_id: str, *, request_id: str,
                    name: str, charter_digest: str) -> dict:
        require_digest(charter_digest)
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 120:
            raise OrganizationError('invalid','invalid cabal name')
        def write(tx):
            cabal_id = uuid4().hex
            tx.insert('cabals',{'project_id':project_id,'cabal_id':cabal_id,
                       'name':name.strip(), 'charter_digest':charter_digest, 'state':'active'})
            return {'cabal_id':cabal_id,'revision':1}
        return self._mutate(actor,project_id,'B.createCabal',request_id,
                            {'name':name,'charter_digest':charter_digest},write)

    def staffCabal(self, actor: TrustedActor, project_id: str, cabal_id: str, *,
                   request_id: str, member_id: str, role_id: str,permission_digest: str) -> dict:
        require_id(cabal_id); require_id(member_id);require_id(role_id);require_digest(permission_digest)
        actor.require(project_id)
        self.policy.check_membership(actor.principal_id,project_id,member_id,role_id,permission_digest)
        def write(tx):
            cabal = tx.one('cabals',project_id=project_id,cabal_id=cabal_id)
            role = tx.one('specialist_roles',role_id=role_id)
            if cabal is None or cabal['state']!='active':
                raise OrganizationError('missing','active cabal not found in this project')
            if role is None or role['state'] != 'ready':
                raise OrganizationError('capability_unavailable','role resources not ready')
            existing = tx.one('cabal_members',project_id=project_id,cabal_id=cabal_id,member_id=member_id)
            if existing:
                raise OrganizationError('conflict','member already staffed; cannot silently expand grant')
            tx.insert('cabal_members',{'project_id':project_id,'cabal_id':cabal_id,
                        'member_id':member_id,'role_id':role_id,'permission_digest':permission_digest})
            return {'cabal_id':cabal_id,'member_id':member_id}
        return self._mutate(actor,project_id,'B.staffCabal',request_id,
                     {'cabal_id':cabal_id,'member_id':member_id,'role_id':role_id,
                      'permission_digest':permission_digest},write)

    def listCabals(self, actor: TrustedActor, project_id: str) -> dict:
        def read(tx):
            return {'cabals':[
                {'cabal_id':row['cabal_id'],'name':row['name'],'state':row['state']}
                for row in tx.many('cabals',project_id=project_id)]}
        return self._read(actor,project_id,read)

    def importRoles(self, actor: TrustedActor, project_id: str, *, request_id: str,
                    catalog_digest: str, role_manifest_digests: list[str]) -> dict:
        """Import verified catalog bytes; absent role resources remain unavailable."""
        require_digest(catalog_digest)
        if not isinstance(role_manifest_digests,list):
            raise OrganizationError('invalid','manifest digests must be list')
        requested = {require_digest(x) for x in role_manifest_digests}
        if len(requested)!=len(role_manifest_digests):
            raise OrganizationError('invalid','duplicate role manifest digest')
        raw = verify_blob(self.source,catalog_digest)
        if raw is None:
            raise OrganizationError('capability_unavailable', 'catalog bytes unavailable')
        catalog = load_json(raw)
        roles, departments = catalog.get('roles'),catalog.get('departments')
        if not isinstance(roles,list) or not isinstance(departments,list):
            raise OrganizationError('invalid','catalog must specify roles and departments')
        deptmap={}
        for dept in departments:
            did = require_id(dept['department_id'])
            if did in deptmap or not isinstance(dept.get('name'),str) or not dept['name']:
                raise OrganizationError('invalid','duplicate or malformed department')
            deptmap[did]=dept['name']
        rowmap={}
        for role in roles:
            role_id = require_id(role['role_id'])
            require_id(role['revision']);require_digest(role['manifest_digest'])
            if role['department_id'] not in deptmap or role_id in rowmap:
                raise OrganizationError('invalid','duplicate role or missing department')
            rowmap[role_id]=role
        if requested - {r['manifest_digest'] for r in roles}:
            raise OrganizationError('invalid','unknown role manifest digest')
        # All roles in the actual catalog are represented, including missing bytes.
        readiness={rid:role_readiness(role,self.source,self.host) for rid,role in rowmap.items()}
        def write(tx):
            for did,name in deptmap.items():
                existing=tx.one('departments',department_id=did)
                if existing and existing['name']!=name:
                    raise OrganizationError('conflict','department ID has another meaning')
                if not existing:
                    tx.insert('departments',{'department_id':did,'name':name})
            count=0
            for rid,role in rowmap.items():
                checked=readiness[rid]
                existing=tx.one('specialist_roles',role_id=rid)
                state=checked.state if role['manifest_digest'] in requested else 'unavailable'
                if existing and existing['state']=='ready' and state=='unavailable' and existing['role_manifest_digest']==role['manifest_digest']:
                    state='ready'  # Global catalog: another project's partial import cannot downgrade verified bytes.
                entry={'role_revision':role['revision'],
                       'role_manifest_digest':role['manifest_digest'],
                       'department_id':role['department_id'],'state':state}
                if existing:
                    if existing['role_manifest_digest']!=role['manifest_digest']:
                        raise OrganizationError('conflict','global role revision changed; explicit upgrade required')
                    tx.change('specialist_roles',{'role_id':rid},entry)
                else:
                    tx.insert('specialist_roles',{'role_id':rid,**entry})
                count+=state=='ready' and role['manifest_digest'] in requested
            return {'catalog_digest':catalog_digest,'installed_count':count}
        return self._mutate(actor,project_id,'B.importRoles',request_id,
            {'catalog_digest':catalog_digest,'role_manifest_digests':role_manifest_digests},write)

    def role_diagnostics(self, actor: TrustedActor, project_id: str,
                         catalog_digest: str) -> dict[str, Any]:
        """Unabridged missing-byte/tool diagnostics beyond frozen listRoles summary."""
        actor.require(project_id); require_digest(catalog_digest)
        raw=verify_blob(self.source,catalog_digest)
        if raw is None:
            raise OrganizationError('capability_unavailable','catalog bytes unavailable')
        catalog=load_json(raw)
        return {r['role_id']:role_readiness(r,self.source,self.host) for r in catalog['roles']}

    def listRoles(self, actor: TrustedActor, project_id: str) -> dict:
        return self._read(actor,project_id,lambda tx:{'roles':[
            {'role_id':r['role_id'],'role_revision':r['role_revision'],'state':r['state']}
            for r in tx.many('specialist_roles')]})

    def listDepartments(self, actor: TrustedActor, project_id: str) -> dict:
        return self._read(actor,project_id,lambda tx:{'departments':[
            {'department_id':r['department_id'],'name':r['name']}
            for r in tx.many('departments')]})

    def createDeliberation(self,actor:TrustedActor,project_id:str,cabal_id:str,*,
                           request_id:str,subject_digest:str,record_digest:str)->dict:
        require_id(cabal_id);require_digest(subject_digest);require_digest(record_digest)
        def write(tx):
            cabal=tx.one('cabals',project_id=project_id,cabal_id=cabal_id)
            if cabal is None or cabal['state']!='active':
                raise OrganizationError('missing','active cabal not found')
            deliberation_id=uuid4().hex
            tx.insert('deliberations',{'project_id':project_id,'cabal_id':cabal_id,
                      'deliberation_id':deliberation_id, 'subject_digest':subject_digest,
                      'record_digest':record_digest,'state':'open'})
            return {'deliberation_id':deliberation_id,'state':'open'}
        return self._mutate(actor,project_id,'B.createDeliberation',request_id,
            {'cabal_id':cabal_id,'subject_digest':subject_digest,'record_digest':record_digest},write)

    def listDeliberations(self,actor:TrustedActor,project_id:str,cabal_id:str)->dict:
        require_id(cabal_id)
        def read(tx):
            cabal=tx.one('cabals',project_id=project_id,cabal_id=cabal_id)
            if cabal is None:
                raise OrganizationError('missing','cabal not found')
            return {'deliberations':[
                {'deliberation_id':r['deliberation_id'],
                 'subject_digest':r['subject_digest'],'state':r['state']}
                for r in tx.many('deliberations',project_id=project_id,cabal_id=cabal_id)]}
        return self._read(actor,project_id,read)

    def listToolGrants(self,actor:TrustedActor,project_id:str)->dict:
        return self._read(actor,project_id,lambda tx:{'grants':[
            {'grant_id':r['grant_id'],'package_digest':r['package_digest'],
             'entrypoint_id':r['entrypoint_id'],'revoked':bool(r['revoked'])}
            for r in tx.many('tool_grants',project_id=project_id)]})

    def revokeToolGrant(self,actor:TrustedActor,project_id:str,grant_id:str,*,
                        request_id:str,expected_state:str)->dict:
        require_id(grant_id)
        if expected_state not in ('active','revoked'):
            raise OrganizationError('invalid','expected_state must be active or revoked')
        def write(tx):
            record=tx.one('tool_grants',project_id=project_id,grant_id=grant_id)
            if record is None:
                raise OrganizationError('missing','tool grant not found')
            if ('revoked' if record['revoked'] else 'active')!=expected_state:
                raise OrganizationError('conflict','tool grant state changed')
            tx.change('tool_grants',{'project_id':project_id,'grant_id':grant_id}, {'revoked':1})
            # Existing external operation completion is not asserted.
            return {'grant_id':grant_id,'revoked':True}
        return self._mutate(actor,project_id,'B.revokeToolGrant',request_id,
                 {'grant_id':grant_id,'expected_state':expected_state},write,owner=True)

    def launch_role_configuration(self, actor: TrustedActor, project_id: str,
                                  cabal_id: str, member_id: str) -> dict:
        """Version-pinned human launch guidance for WG-I, never an auth token.

        Caller must separately pin task/Canon and verify project policy + lease.
        """
        require_id(cabal_id); require_id(member_id)
        def read(tx):
            membership=tx.one('cabal_members',project_id=project_id,
                              cabal_id=cabal_id,member_id=member_id)
            cabal=tx.one('cabals',project_id=project_id,cabal_id=cabal_id)
            if not membership or not cabal or cabal['state']!='active':
                raise OrganizationError('missing','active staffed cabal member required')
            role=tx.one('specialist_roles',role_id=membership['role_id'])
            if not role or role['state']!='ready':
                raise OrganizationError('capability_unavailable','role not ready')
            manifest=verify_blob(self.source,role['role_manifest_digest'])
            if manifest is None:
                raise OrganizationError('capability_unavailable','pinned role bytes missing')
            role_data=load_json(manifest)
            if role_data.get('revision')!=role['role_revision'] or role_data.get('role_id')!=role['role_id']:
                raise OrganizationError('invalid','role manifest revision mismatch')
            if role_readiness({'role_id':role['role_id'],
                               'revision':role['role_revision'],
                               'manifest_digest':role['role_manifest_digest']},self.source,self.host).state!='ready':
                raise OrganizationError('capability_unavailable','role lost executable readiness')
            packages={p['package_digest'] for p in tx.many('project_packages',project_id=project_id)
                      if p['enabled']}
            grants=[{'grant_id':g['grant_id'],'package_digest':g['package_digest'],
                     'entrypoint_id':g['entrypoint_id'],'workspace_id':g['workspace_id'],
                     'grant_digest':g['grant_digest']}
                    for g in tx.many('tool_grants',project_id=project_id)
                    if not g['revoked'] and g['package_digest'] in packages]
            return {'project_id':project_id,'cabal_id':cabal_id,'member_id':member_id,
                    'role_id':role['role_id'],'role_revision':role['role_revision'],
                    'role_manifest_digest':role['role_manifest_digest'],
                    'permission_digest':membership['permission_digest'],
                    'reasoning_tier':role_data.get('reasoning_tier'),
                    'personality':role_data.get('personality'),
                    'enabled_package_digests':sorted(packages),
                    'tool_grants':grants,'authorization':'REFERENCE_ONLY_NOT_CREDENTIALS'}
        return self._read(actor,project_id,read)

    def build_cabal_relay_draft(self, actor: TrustedActor, project_id: str,
                                cabal_id: str, *, kind: str, content_digest: str,
                                target_session_id: str) -> dict:
        """Draft only. WG-G/I authenticate, publish and observe actual GitHub relays."""
        if kind not in ('ASSIGN','QUESTION','ANSWER','HANDOFF','REVIEW','STOP'):
            raise OrganizationError('invalid','unsupported cabal relay kind')
        require_id(cabal_id);require_id(target_session_id);require_digest(content_digest)
        def read(tx):
            cabal=tx.one('cabals',project_id=project_id,cabal_id=cabal_id)
            if not cabal or cabal['state']!='active':
                raise OrganizationError('missing','active cabal not found')
            return {'project_id':project_id, 'cabal_id':cabal_id,'kind':kind,
                    'payload_digest':content_digest,'to_session_id':target_session_id,
                    'delivery_state':'DRAFT_NOT_PUBLISHED'}
        return self._read(actor,project_id,read)
