"""R9 CAB-C contract validation and deterministic work-graph algorithms.

No IO, provider effects or untrusted clock decisions are performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping
import json
import re

ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
DIGEST = re.compile(r'^[a-f0-9]{64}$')
KINDS = frozenset({'NARRATIVE','QUEST_GRAPH','DIALOGUE','SCENE','CODE','ASSET','AUDIO','ANIMATION','TEST','OTHER'})
CODES = frozenset({'OK','ACCEPTED_PENDING','INVALID_INPUT','NOT_FOUND','UNAUTHENTICATED',
    'PERMISSION_DENIED','WRONG_PROJECT','WRONG_FEATURE','STALE_BIBLE','BIBLE_CONFLICT',
    'SCOPE_EXCEEDED','OWNER_ACTION_REQUIRED','REVISION_CONFLICT','IDEMPOTENCY_CONFLICT',
    'LEASE_CONFLICT','LEASE_FENCED','SOURCE_CONFLICT','PROVIDER_UNKNOWN',
    'PROVIDER_UNSUPPORTED','PROVIDER_NOT_VERIFIED','TEST_FAILED','TEST_NOT_RUN',
    'EVIDENCE_INVALID','REVIEW_CONFLICT','CAPACITY_LIMIT','BACKPRESSURE',
    'LOCAL_RECOVERY_REQUIRED','UNSUPPORTED_OPERATION','RELEASE_BLOCKED'})


from nomad_studio.contracts.runtime_ports_v1 import Result, Decision


class Refused(Exception):
    def __init__(self, code: str, status: str = 'REJECTED') -> None:
        if code not in CODES or status not in {'REJECTED','CONFLICT','NOT_FOUND','BLOCKED_LOCAL','RETRYABLE','UNKNOWN_EXTERNAL'}:
            raise ValueError('invalid R9 result status/code')
        self.code, self.status = code, status
        super().__init__(f'{status}:{code}')


def refused(exc: Refused) -> Result:
    return Result(exc.status, exc.code)


def require(check: bool, code: str = 'INVALID_INPUT', status: str = 'REJECTED') -> None:
    if not check:
        raise Refused(code, status)


def ident(v: Any) -> str:
    require(isinstance(v,str) and ID.fullmatch(v) is not None)
    return v


def digest(v: Any) -> str:
    require(isinstance(v,str) and DIGEST.fullmatch(v) is not None)
    return v


def integer(v: Any, low: int = 0) -> int:
    require(type(v) is int and v >= low)
    return v


def closed(v: Any, keys: set[str], required: set[str]) -> Mapping[str, Any]:
    require(isinstance(v, Mapping) and required <= v.keys() and v.keys() <= keys)
    return v


def ids(v: Any, *, minimum: int = 0) -> list[str]:
    require(type(v) is list and minimum <= len(v) <= 4096)
    values = [ident(x) for x in v]
    require(len(values) == len(set(values)))
    return values


def digests(v: Any) -> list[str]:
    require(type(v) is list)
    values = [digest(x) for x in v]
    require(len(values) == len(set(values)))
    return values


def resource_key(v: Any) -> str:
    """Canonical path key; reject alias/traversal and platform-dependent spellings."""
    ident(v)
    require(not v.startswith('/') and not v.endswith('/') and not re.match(r'^[A-Za-z]:', v) and '\\' not in v
            and '\x00' not in v and '//' not in v)
    segments=v.split('/')
    require(all(x not in ('.','..','') and x.casefold()!='.git' for x in segments))
    require(all(not x.endswith(('.',' ')) and not x.startswith(' ') for x in segments))
    return v.casefold()


def canonical(v: Any) -> str:
    try:
        return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise Refused('INVALID_INPUT') from exc


def hash_record(v: Any) -> str:
    return sha256(canonical(v).encode('ascii')).hexdigest()


CONTRACT_KEYS = {'project_id','feature_id','brief_id','original_brief_digest',
 'bible_pair_digest','revision','contract_digest','scope_digest','deliverables',
 'acceptance_ids','budget_limit_units','required_capabilities','unresolved_boundary_refs',
 'repository_ref','feature_branch'}
DELIVERABLE_KEYS = {'deliverable_id','kind','description','requirement_refs','acceptance_ids','required'}
TASK_KEYS = {'project_id','feature_id','task_id','graph_revision','cabal_id',
 'bible_pair_digest','feature_contract_digest','dependencies','required_skills',
 'owned_resources','input_digests','outputs','acceptance_ids','budget_limit_units','task_digest'}
GRAPH_KEYS = {'graph_id','project_id','feature_id','revision','bible_pair_digest',
 'feature_contract_digest','graph_digest','tasks'}
PLAN_KEYS = {'task_id','cabal_id','dependencies','required_skills','owned_resources',
 'input_digests','outputs','acceptance_ids','budget_limit_units'}


def validate_contract(v: Any) -> Mapping[str, Any]:
    c=closed(v,CONTRACT_KEYS, CONTRACT_KEYS-{'repository_ref','feature_branch'})
    for k in ('project_id','feature_id','brief_id'): ident(c[k])
    for k in ('original_brief_digest','bible_pair_digest','contract_digest','scope_digest'): digest(c[k])
    integer(c['revision'],1); integer(c['budget_limit_units'])
    ids(c['acceptance_ids']); ids(c['required_capabilities']); ids(c['unresolved_boundary_refs'])
    for k in ('repository_ref','feature_branch'):
        if k in c: require(type(c[k]) is str and 1 <= len(c[k]) <= 16384)
    require(type(c['deliverables']) is list and len(c['deliverables']) > 0)
    dids=[]
    for d in c['deliverables']:
        closed(d,DELIVERABLE_KEYS,DELIVERABLE_KEYS)
        dids.append(ident(d['deliverable_id']))
        require(d['kind'] in KINDS and isinstance(d['description'],str) and 1<=len(d['description'])<=16384)
        ids(d['requirement_refs']); ids(d['acceptance_ids'])
        require(type(d['required']) is bool)
        require(set(d['acceptance_ids']) <= set(c['acceptance_ids']))
    require(len(set(dids))==len(dids))
    return c


def validate_task(t: Any) -> Mapping[str, Any]:
    closed(t,TASK_KEYS,TASK_KEYS)
    for k in ('project_id','feature_id','task_id','cabal_id'): ident(t[k])
    for k in ('bible_pair_digest','feature_contract_digest','task_digest'): digest(t[k])
    integer(t['graph_revision'],1); integer(t['budget_limit_units'])
    for k in ('dependencies','required_skills','owned_resources','outputs','acceptance_ids'): ids(t[k])
    digests(t['input_digests'])
    require(t['task_id'] not in t['dependencies'])
    require(hash_record({k:v for k,v in t.items() if k!='task_digest'}) == t['task_digest'])
    return t


def validate_graph(g: Any, contract_digest: str, *, deliverables: set[str] | None = None,
                   acceptance: set[str] | None = None, budget: int | None = None) -> Mapping[str,Any]:
    closed(g,GRAPH_KEYS,GRAPH_KEYS)
    for k in ('graph_id','project_id','feature_id'): ident(g[k])
    for k in ('bible_pair_digest','feature_contract_digest','graph_digest'): digest(g[k])
    integer(g['revision'],1)
    require(g['feature_contract_digest']==digest(contract_digest),'REVISION_CONFLICT','CONFLICT')
    require(type(g['tasks']) is list and len(g['tasks'])>0)
    require(hash_record({k:v for k,v in g.items() if k!='graph_digest'}) == g['graph_digest'])
    tasks={}
    owners={}
    consumed=set()
    for t in g['tasks']:
        validate_task(t)
        require(t['project_id']==g['project_id'],'WRONG_PROJECT')
        require(t['feature_id']==g['feature_id'],'WRONG_FEATURE')
        require(t['bible_pair_digest']==g['bible_pair_digest'],'STALE_BIBLE')
        require(t['feature_contract_digest']==contract_digest and t['graph_revision']==g['revision'],'REVISION_CONFLICT','CONFLICT')
        require(t['task_id'] not in tasks)
        tasks[t['task_id']]=t
        for output in t['outputs']:
            require(output not in consumed)
            consumed.add(output)
        local=set()
        for owned in t['owned_resources']:
            resource=resource_key(owned)
            require(resource not in local)
            local.add(resource)
            owners.setdefault(resource,[]).append(t['task_id'])
        if acceptance is not None: require(set(t['acceptance_ids'])<=acceptance)
    if deliverables is not None: require(consumed==deliverables)
    if budget is not None: require(sum(t['budget_limit_units'] for t in tasks.values())<=budget,'CAPACITY_LIMIT')
    children={key:[] for key in tasks}
    for t in tasks.values():
        for dep in t['dependencies']:
            require(dep in tasks, 'WRONG_FEATURE')
            children[dep].append(t['task_id'])
    indegree={k:len(t['dependencies']) for k,t in tasks.items()}
    from heapq import heappop, heappush
    queue=[]
    for k,n in indegree.items():
        if n==0: heappush(queue,k)
    topo=[]
    while queue:
        key=heappop(queue);topo.append(key)
        for child in children[key]:
            indegree[child]-=1
            if indegree[child]==0: heappush(queue,child)
    require(len(topo)==len(tasks))  # Cycle, including deeply nested malicious DAGs.
    ancestor={k:set() for k in tasks}
    for key in topo:
        for dep in tasks[key]['dependencies']:
            ancestor[key].add(dep)
            ancestor[key].update(ancestor[dep])
    # Sorted path stack finds every ancestor/descendant collision without N^2
    # comparisons of unrelated resources. A path may have multiple owners.
    stack=[]
    for resource,tids in sorted(owners.items()):
        while stack and not (resource==stack[-1] or resource.startswith(stack[-1]+'/')):
            stack.pop()
        for prefix in stack:
            for a in owners[prefix]:
                for b in tids:
                    require(a==b or a in ancestor[b] or b in ancestor[a],
                            'LEASE_CONFLICT','CONFLICT')
        for i,a in enumerate(tids):
            for b in tids[i+1:]:
                require(a in ancestor[b] or b in ancestor[a], 'LEASE_CONFLICT','CONFLICT')
        if not stack or stack[-1]!=resource:
            stack.append(resource)
    return g


def critical_path(graph: Mapping[str,Any], completed: set[str]) -> list[dict[str,Any]]:
    """Longest downstream path without recursion, even for huge production DAGs."""
    tasks={t['task_id']:t for t in graph['tasks']}
    children={k:[] for k in tasks}
    indegree={k:len(t['dependencies']) for k,t in tasks.items()}
    for key,t in tasks.items():
        for dep in t['dependencies']: children[dep].append(key)
    from heapq import heappop, heappush
    queue=[]
    for k,n in indegree.items():
        if n==0: heappush(queue,k)
    topo=[]
    while queue:
        key=heappop(queue);topo.append(key)
        for child in children[key]:
            indegree[child]-=1
            if indegree[child]==0: heappush(queue,child)
    require(len(topo)==len(tasks))
    depth={}
    for key in reversed(topo):
        depth[key]=1+max((depth[c] for c in children[key]),default=0)
    return [{'task_id':k,'remaining_critical_path':depth[k],
             'unmet_dependencies':sorted(set(t['dependencies'])-completed)}
            for k,t in sorted(tasks.items(),key=lambda x:(-depth[x[0]],x[0]))]


def make_graph(contract: Mapping[str,Any], snapshot: Mapping[str,Any], revision: int) -> dict[str,Any]:
    validate_contract(contract)
    required={'project_id','feature_id','bible_pair_digest','feature_contract_digest','tasks'}
    closed(snapshot, required,required)
    for field,target in [('project_id','project_id'),('feature_id','feature_id')]:
        require(snapshot[field]==contract[target],'WRONG_'+field.split('_')[0].upper())
    require(snapshot['bible_pair_digest']==contract['bible_pair_digest'],'STALE_BIBLE')
    require(snapshot['feature_contract_digest']==contract['contract_digest'],'REVISION_CONFLICT','CONFLICT')
    plans=snapshot['tasks']
    require(type(plans) is list and len(plans)>0)
    feature=contract['feature_id']; project=contract['project_id']
    namespace=f'{feature}:r{revision}:'
    baseids=[]
    for p in plans:
        closed(p,PLAN_KEYS,PLAN_KEYS)
        baseids.append(ident(p['task_id']))
    require(len(baseids)==len(set(baseids)))
    lookup={key:namespace+key for key in baseids}
    for key in lookup.values(): ident(key)
    tasks=[]
    for p in plans:
        ids(p['dependencies']); ids(p['required_skills']); ids(p['owned_resources']); ids(p['outputs']); ids(p['acceptance_ids'])
        digests(p['input_digests']); integer(p['budget_limit_units'])
        require(set(p['required_skills'])<=set(contract['required_capabilities']))
        require(all(dep in lookup for dep in p['dependencies']),'WRONG_FEATURE')
        t={'project_id':project,'feature_id':feature,'task_id':lookup[p['task_id']],
           'graph_revision':revision,'cabal_id':ident(p['cabal_id']),
           'bible_pair_digest':contract['bible_pair_digest'],
           'feature_contract_digest':contract['contract_digest'],
           'dependencies':[lookup[x] for x in p['dependencies']],
           'required_skills':sorted(p['required_skills']),
           'owned_resources':sorted(p['owned_resources']),
           'input_digests':sorted(p['input_digests']),
           'outputs':sorted(p['outputs']),
           'acceptance_ids':sorted(p['acceptance_ids']),
           'budget_limit_units':p['budget_limit_units']}
        t['task_digest']=hash_record(t)
        tasks.append(t)
    g={'graph_id':f'{feature}:r{revision}','project_id':project,'feature_id':feature,
       'revision':revision,'bible_pair_digest':contract['bible_pair_digest'],
       'feature_contract_digest':contract['contract_digest'],'tasks':tasks}
    g['graph_digest']=hash_record(g)
    validate_graph(g,contract['contract_digest'],
                   deliverables={d['deliverable_id'] for d in contract['deliverables']},
                   acceptance=set(contract['acceptance_ids']),budget=contract['budget_limit_units'])
    return g
