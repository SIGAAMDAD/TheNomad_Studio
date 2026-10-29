"""R9 CapabilityPort: installed skills, project cabal capacity and bounded placement.

P2 injects a table-scoped SQLite reader/writer, a trusted installed skill
provider, a trusted worker snapshot, and a trusted active-bible reader. This
module does not make network calls, authenticate callers, or issue job leases.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from hashlib import sha256
import math
import re
from typing import Any

from .catalog import Catalog, Specialization, load_source_roster
from .effort import select_effort
from .results import Result
from .skills import SkillVerificationError, verify_installed_skill, verify_adapter_manifest
from .validation import valid_records

_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
_DIGEST = re.compile(r'^[a-f0-9]{64}$')


def _id(v: Any) -> bool:
    return isinstance(v,str) and bool(_ID.fullmatch(v))


def _digest(v: Any) -> bool:
    return isinstance(v,str) and bool(_DIGEST.fullmatch(v))


def _row(r: Any, sql: str, args: tuple = ()) -> dict | None:
    c = r.execute(sql,args).fetchone()
    return dict(c) if c is not None else None


def _key(project_id: str, skill_id: str) -> str:
    # Frozen skill_id global PK with no compound project scope. Digest-addressed
    # namespace supports maximum-length IDs with no overlength/cross-project alias.
    if not _id(project_id) or not _id(skill_id):
        raise ValueError('invalid project or skill identity')
    return 'skill:'+sha256((project_id+'\x00'+skill_id).encode('utf-8')).hexdigest()


class CapabilityService:
    def __init__(self, deps: Mapping[str, Any]):
        self.reader = deps['reader']  # .read(fn), already read-only/scoped by P2
        self.writer = deps['writer']  # .write(fn), only CAB-G table identities
        self.installed_reader = deps.get('installed_skill_reader')
        self.worker_observer = deps.get('worker_observer')
        self.active_bible = deps.get('active_bible_reader')
        self.adapter_reader = deps.get('adapter_reader')
        self.project_catalogs: dict[str, Catalog] = dict(deps.get('project_catalogs',{}))
        from nomad_studio.budget.api import BudgetService
        self._budget = BudgetService(deps)

    def import_project_roster(self, project_id: str, catalog: Catalog) -> Result:
        """Persist full roster as uninstalled project data, never authority.

        Reimporting an identical catalog after restart is safe. Existing
        installed bytes/evidence are not downgraded to roster-only state.
        """
        if not _id(project_id) or not isinstance(catalog,Catalog) or catalog.project_id != project_id:
            return Result('REJECTED','INVALID_INPUT')
        old=self.project_catalogs.get(project_id)
        if old is not None and old != catalog:
            return Result('CONFLICT','REVISION_CONFLICT')
        def write(conn: Any) -> None:
            for item in catalog.specializations.values():
                db_id=_key(project_id,item.id)
                if _row(conn,'SELECT skill_id FROM v1_skill_capabilities WHERE skill_id=?',(db_id,)):
                    continue
                record={'skill_id':item.id,'uri':item.skill_uri,'version':item.skill_version,
                        'title':item.title,'provenance':item.provenance,
                        'source_archive_sha256':item.source_archive_sha256,
                        'installed':False,'project_adapter':False}
                conn.execute('INSERT INTO v1_skill_capabilities(skill_id,source_digest,qualification_tier,project_scope_id,payload_json) '
                    'VALUES(?,?,?,?,?)',(db_id,catalog.source_digests[0] if catalog.source_digests else '0'*64,
                        'roster_only',project_id,json.dumps(record,sort_keys=True,separators=(',',':'))))
        try:
            self.writer.write(write)
        except Exception:
            return Result('BLOCKED_LOCAL','LOCAL_RECOVERY_REQUIRED')
        self.project_catalogs[project_id]=catalog
        return Result('OK','OK',{'project_id':project_id,'specialization_count':len(catalog.specializations),
                                'specialization_ids':sorted(catalog.specializations)})

    def install_skill(self, project_id: str, skill_id: str, pin: Mapping[str,Any],
                      qualification_tier: str = 'verified') -> Result:
        """Install a skill verified against *actual reader bytes* into frozen DDL.

        `project_catalogs` is the project data source; a roster entry itself
        cannot establish installation. No scope expansion or arbitrary SQL.
        """
        c=self.project_catalogs.get(project_id)
        if not c or skill_id not in c.specializations or qualification_tier not in ('verified','advanced','expert'):
            return Result('REJECTED','INVALID_INPUT')
        if self.installed_reader is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            evidence=verify_installed_skill(c.specializations[skill_id],pin,self.installed_reader)
            evidence['installed']=True
        except SkillVerificationError:
            return Result('NOT_VERIFIED','EVIDENCE_INVALID')
        def write(conn: Any) -> None:
            db_id=_key(project_id,skill_id)
            existing = _row(conn,'SELECT * FROM v1_skill_capabilities WHERE skill_id=?',(db_id,))
            if existing and existing['source_digest'] != evidence['source_digest']:
                conn.execute('UPDATE v1_worker_capabilities SET available=0 WHERE skill_id=?',(db_id,))
            payload=json.dumps(evidence,sort_keys=True,separators=(',',':'))
            conn.execute('INSERT INTO v1_skill_capabilities(skill_id,source_digest,qualification_tier,project_scope_id,payload_json) '
                'VALUES(?,?,?,?,?) ON CONFLICT(skill_id) DO UPDATE SET source_digest=excluded.source_digest, '
                'qualification_tier=excluded.qualification_tier,payload_json=excluded.payload_json',
                (db_id,evidence['source_digest'],qualification_tier,project_id,payload))
        try:
            self.writer.write(write)
        except ValueError:
            return Result('REJECTED','WRONG_PROJECT')
        except Exception:
            return Result('BLOCKED_LOCAL','LOCAL_RECOVERY_REQUIRED')
        return Result('OK','OK',evidence,evidence_refs=(evidence['evidence_ref'],))

    def install_project_adapter(self, project_id: str, manifest: Mapping[str,Any]) -> Result:
        """Adapter data, not hardcoded Godot/FMOD behavior or global permissions."""
        if not _id(project_id) or self.adapter_reader is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            # Ignore caller's purported proof: independently read trusted manifest.
            trusted=self.adapter_reader(project_id,manifest.get('adapter_id'))
            a=verify_adapter_manifest(trusted)
            if (manifest.get('source_digest') != a['source_digest'] or
                manifest.get('capabilities') != list(a['capabilities'])):
                raise SkillVerificationError('requested adapter does not match trusted provider')
        except Exception:
            return Result('NOT_VERIFIED','EVIDENCE_INVALID')
        try:
            def write(conn: Any) -> None:
                present=set()
                for row in conn.execute('SELECT skill_id,payload_json FROM v1_skill_capabilities WHERE project_scope_id=?',(project_id,)):
                    existing=json.loads(row['payload_json'])
                    if existing.get('adapter_id') == a['adapter_id']:
                        present.add(row['skill_id'])
                new_keys={_key(project_id,f"{a['adapter_id']}:{cap}") for cap in a['capabilities']}
                for old_key in present-new_keys:
                    conn.execute('UPDATE v1_worker_capabilities SET available=0 WHERE skill_id=?',(old_key,))
                    old=_row(conn,'SELECT payload_json FROM v1_skill_capabilities WHERE skill_id=?',(old_key,))
                    removed=json.loads(old['payload_json'])
                    removed['installed']=False
                    conn.execute('UPDATE v1_skill_capabilities SET qualification_tier=?,payload_json=? WHERE skill_id=?',
                        ('revoked',json.dumps(removed,sort_keys=True),old_key))
                for cap in a['capabilities']:
                    skill_id=f"{a['adapter_id']}:{cap}"
                    if not _id(skill_id):
                        raise ValueError('adapter skill id overflow')
                    db_id=_key(project_id,skill_id)
                    old=_row(conn,'SELECT source_digest FROM v1_skill_capabilities WHERE skill_id=?',(db_id,))
                    if old and old['source_digest'] != a['source_digest']:
                        conn.execute('UPDATE v1_worker_capabilities SET available=0 WHERE skill_id=?',(db_id,))
                    payload=json.dumps({'skill_id':skill_id,'adapter_id':a['adapter_id'],
                        'evidence_ref':a['evidence_ref'],'project_adapter':True,'installed':True},sort_keys=True)
                    conn.execute('INSERT INTO v1_skill_capabilities(skill_id,source_digest,qualification_tier,project_scope_id,payload_json) '
                        'VALUES(?,?,?,?,?) ON CONFLICT(skill_id) DO UPDATE SET source_digest=excluded.source_digest, '
                        'payload_json=excluded.payload_json',
                        (db_id,a['source_digest'],'verified',project_id,payload))
            self.writer.write(write)
        except ValueError:
            return Result('REJECTED','WRONG_PROJECT')
        except Exception:
            return Result('BLOCKED_LOCAL','LOCAL_RECOVERY_REQUIRED')
        return Result('OK','OK',{'project_id':project_id,'installed':list(a['capabilities'])},
                      evidence_refs=(a['evidence_ref'],))

    def sync_worker_qualification(self, worker_id: str, skill_id: str, project_id: str) -> Result:
        """Trusted observation, not a worker-supplied credential or a persona."""
        if not _id(worker_id) or not _id(skill_id) or not _id(project_id) or self.worker_observer is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            observed=self.worker_observer(project_id)
            info=next(w for w in observed['workers'] if w['worker_id']==worker_id and w.get('project_id')==project_id)
            if (observed.get('observed') is not True or info.get('observed') is not True or
                skill_id not in info['verified_skills'] or not _id(info.get('evidence_ref'))):
                return Result('NOT_VERIFIED','EVIDENCE_INVALID')
        except Exception:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        def write(conn: Any) -> None:
            db_id=_key(project_id,skill_id)
            entry=_row(conn,'SELECT payload_json,qualification_tier FROM v1_skill_capabilities WHERE skill_id=?',(db_id,))
            if not entry or entry['qualification_tier']=='roster_only' or not json.loads(entry['payload_json']).get('installed'):
                raise ValueError('skill uninstalled')
            conn.execute('INSERT INTO v1_worker_capabilities(worker_id,skill_id,verified_evidence_ref,available) '
                'VALUES(?,?,?,1) ON CONFLICT(worker_id,skill_id) DO UPDATE SET '
                'verified_evidence_ref=excluded.verified_evidence_ref,available=1',
                (worker_id,db_id,info['evidence_ref']))
        try:
            self.writer.write(write)
        except ValueError:
            return Result('BLOCKED_LOCAL','PROVIDER_NOT_VERIFIED')
        except Exception:
            return Result('BLOCKED_LOCAL','LOCAL_RECOVERY_REQUIRED')
        return Result('OK','OK',{'worker_id':worker_id,'skill_id':skill_id},
                      evidence_refs=(info['evidence_ref'],))

    def resolve_capabilities(self, project_id: str, required_skills: Sequence[str], bible_pair_digest: str) -> Result:
        if (not _id(project_id) or not _digest(bible_pair_digest) or
            isinstance(required_skills,(str,bytes)) or not isinstance(required_skills,Sequence) or
            any(not _id(s) for s in required_skills) or len(set(required_skills)) != len(required_skills)):
            return Result('REJECTED','INVALID_INPUT')
        if self.active_bible is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            current=self.active_bible(project_id)
            if current is None:
                return Result('NOT_FOUND','NOT_FOUND')
            if current != bible_pair_digest:
                return Result('REJECTED','STALE_BIBLE')
            def read(conn: Any) -> dict:
                verified={}; missing=[]; pending=list(required_skills); seen=set()
                while pending:
                    skill_id=pending.pop()
                    if skill_id in seen:
                        continue
                    seen.add(skill_id)
                    if len(seen)>4096:
                        raise ValueError('excessive skill requirements')
                    row=_row(conn,'SELECT * FROM v1_skill_capabilities WHERE skill_id=?',(_key(project_id,skill_id),))
                    if row is None or row['project_scope_id'] != project_id:
                        missing.append(skill_id)
                    else:
                        payload=json.loads(row['payload_json'])
                        if (row['qualification_tier']=='roster_only' or not payload.get('installed') or
                            not _digest(row['source_digest']) or not _id(payload.get('evidence_ref'))):
                            missing.append(skill_id)
                        else:
                            dependencies=payload.get('requirements',[])
                            if (not isinstance(dependencies,list) or
                                any(not _id(dep) for dep in dependencies)):
                                raise ValueError('invalid installed skill dependency')
                            pending.extend(dependencies)
                            verified[skill_id]={'source_digest':row['source_digest'],
                                'evidence_ref':payload['evidence_ref'],'qualification_tier':row['qualification_tier']}
                return {'project_id':project_id,'bible_pair_digest':bible_pair_digest,
                    'verified':verified,'missing':missing}
            data=self.reader.read(read)
        except Exception:
            return Result('BLOCKED_LOCAL','LOCAL_RECOVERY_REQUIRED')
        refs=tuple(v['evidence_ref'] for v in data['verified'].values())
        if data['missing']:
            return Result('BLOCKED_LOCAL','PROVIDER_NOT_VERIFIED',data,evidence_refs=refs)
        return Result('OK','OK',data,evidence_refs=refs)

    def allocate_capabilities(self, feature_contract: Mapping[str,Any], graph: Mapping[str,Any]) -> Result:
        """Pure scheduling proposal; CAB-C remains sole task/lease writer.

        Uses pinned skill identities + independently observed capacity. Missing
        skills hold their tasks only; limits account for already active WIP and
        proposals selected earlier in this same call.
        """
        if not valid_records(feature_contract,graph):
            return Result('REJECTED','INVALID_INPUT')
        project_id=feature_contract['project_id']
        bible=self.resolve_capabilities(project_id,(),feature_contract['bible_pair_digest'])
        if bible.status != 'OK':
            return bible
        if self.worker_observer is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            snapshot=self.worker_observer(project_id)
            if (not isinstance(snapshot,Mapping) or snapshot.get('observed') is not True or
                not _id(snapshot.get('evidence_ref')) or not isinstance(snapshot.get('workers'),list)):
                return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
            def read(conn: Any) -> tuple[dict,dict]:
                skills={}
                for row in conn.execute('SELECT skill_id,project_scope_id,source_digest,payload_json FROM v1_skill_capabilities'):
                    x=dict(row)
                    payload=json.loads(x['payload_json'])
                    if x['source_digest'] and payload.get('installed'):
                        skills[x['skill_id']]=(x['project_scope_id'],x['source_digest'],payload.get('requirements',[]))
                worker_skills={}
                for row in conn.execute('SELECT worker_id,skill_id FROM v1_worker_capabilities WHERE available=1'):
                    worker_skills.setdefault(row['worker_id'],set()).add(row['skill_id'])
                capacities={(r['project_id'],r['cabal_id'],r['resource_class']):
                    (r['capacity_units'],r['reserved_units']) for r in
                    (dict(x) for x in conn.execute('SELECT * FROM v1_cabal_capacity').fetchall())}
                return (skills,worker_skills),capacities
            (registered,qualified), capacities=self.reader.read(read)
        except Exception:
            return Result('BLOCKED_LOCAL','LOCAL_RECOVERY_REQUIRED')
        try:
            workers={}
            for w in snapshot['workers']:
                if (not isinstance(w,Mapping) or not _id(w.get('worker_id')) or not _id(w.get('cabal_id')) or
                    w.get('observed') is not True or not _id(w.get('evidence_ref')) or
                    type(w.get('active_wip')) is not int or w['active_wip'] < 0 or
                    type(w.get('wip_limit')) is not int or w['wip_limit'] < 0 or
                    type(w.get('session_quota')) is not int or w['session_quota'] < 0 or
                    type(w.get('active_sessions')) is not int or w['active_sessions'] < 0 or
                    not isinstance(w.get('offered_efforts'),list) or
                    w.get('available') is not True or
                    not isinstance(w.get('success_rate'),(int,float)) or
                    not math.isfinite(w['success_rate']) or not 0 <= w['success_rate'] <= 1 or
                    w['worker_id'] in workers):
                    return Result('NOT_VERIFIED','EVIDENCE_INVALID')
                workers[w['worker_id']]=w
            assigned: dict[str,int]={}; cabal_assigned: dict[str,int]={}
            task_wip=snapshot.get('task_wip',{})
            task_limits=snapshot.get('task_wip_limits',{})
            if (not isinstance(task_wip,Mapping) or not isinstance(task_limits,Mapping) or
                any(not _id(k) or type(v) is not int or v<0 for k,v in task_wip.items()) or
                any(not _id(k) or type(v) is not int or v<0 for k,v in task_limits.items())):
                return Result('NOT_VERIFIED','EVIDENCE_INVALID')
            allocations=[]; held=[]
            for task in graph['tasks']:
                if (not isinstance(task,Mapping) or not _id(task.get('task_id')) or
                    not _id(task.get('cabal_id')) or
                    task.get('project_id') != project_id or task.get('feature_id') != graph['feature_id'] or
                    task.get('bible_pair_digest') != graph['bible_pair_digest'] or
                    task.get('feature_contract_digest') != graph['feature_contract_digest'] or
                    task.get('graph_revision') != graph['revision'] or
                    not isinstance(task.get('required_skills'),list) or
                    any(not _id(s) for s in task['required_skills'])):
                    return Result('REJECTED','INVALID_INPUT')
                missing=[]
                needed=list(task['required_skills']); checked=set()
                while needed:
                    dep=needed.pop()
                    if dep in checked:
                        continue
                    checked.add(dep)
                    details=registered.get(_key(project_id,dep))
                    if details is None or details[0] != project_id:
                        missing.append(dep)
                    else:
                        needed.extend(details[2])
                if missing:
                    held.append({'task_id':task['task_id'],'code':'PROVIDER_NOT_VERIFIED','missing':missing})
                    continue
                if task_wip.get(task['task_id'],0) >= task_limits.get(task['task_id'],1):
                    held.append({'task_id':task['task_id'],'code':'CAPACITY_LIMIT'})
                    continue
                cap=capacities.get((project_id,task['cabal_id'],'task'))
                if not cap:
                    held.append({'task_id':task['task_id'],'code':'CAPACITY_LIMIT'})
                    continue
                left=cap[0]-cap[1]-cabal_assigned.get(task['cabal_id'],0)
                if left <= 0:
                    held.append({'task_id':task['task_id'],'code':'CAPACITY_LIMIT'})
                    continue
                # Classification is supplied by the trusted observation port,
                # never from extra unvalidated TaskSpec fields (frozen schema).
                task_class=snapshot.get('task_classes',{}).get(task['task_id'],'production')
                if task_class not in ('production','lead','qa','debug','status'):
                    return Result('NOT_VERIFIED','EVIDENCE_INVALID')
                eligible=[]
                for w in workers.values():
                    if (w['cabal_id'] != task['cabal_id'] or w.get('project_id') != project_id or
                        not {_key(project_id,s) for s in checked}.issubset(qualified.get(w['worker_id'],set()))):
                        continue
                    effort=select_effort(task_class,w['offered_efforts'],persistent=task_class!='status')
                    if not effort:
                        continue
                    observed_limit=min(w['wip_limit'],w['session_quota'],max(1,math.floor(w['wip_limit']*w['success_rate'])))
                    if w['active_wip']+assigned.get(w['worker_id'],0) >= observed_limit:
                        continue
                    if w['active_sessions']+assigned.get(w['worker_id'],0) >= w['session_quota']:
                        continue
                    eligible.append((w['success_rate'],-w['active_wip']-assigned.get(w['worker_id'],0),w['worker_id'],effort,w))
                if not eligible:
                    held.append({'task_id':task['task_id'],'code':'CAPACITY_LIMIT'})
                    continue
                _,_,worker_id,effort,w=max(eligible)
                assigned[worker_id]=assigned.get(worker_id,0)+1
                cabal_assigned[task['cabal_id']]=cabal_assigned.get(task['cabal_id'],0)+1
                allocations.append({'task_id':task['task_id'],'worker_id':worker_id,
                    'cabal_id':task['cabal_id'],'effort_target':effort,'observation_ref':w['evidence_ref']})
            result={'project_id':project_id,'feature_id':graph['feature_id'],
                'graph_revision':graph['revision'],'proposals':allocations,'held_tasks':held,
                'lease_committed':False,'observation_ref':snapshot['evidence_ref']}
            return Result('OK','OK',result,evidence_refs=(snapshot['evidence_ref'],))
        except (ValueError,TypeError,KeyError):
            return Result('NOT_VERIFIED','EVIDENCE_INVALID')

    def reserve_budget(self, feature_id: str, requested_units: int, feature_budget_digest: str):
        return self._budget.reserve_budget(feature_id,requested_units,feature_budget_digest)

    def reconcile_spend(self, reservation_id: str, provider_charge_receipt: Mapping[str,Any]):
        return self._budget.reconcile_spend(reservation_id,provider_charge_receipt)


def build_capability_port(deps: Mapping[str,Any]) -> CapabilityService:
    """P2 composition seam: all four frozen CapabilityPort methods."""
    return CapabilityService(deps)
