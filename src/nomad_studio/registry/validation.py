"""Strict frozen R9 WorkGraph/TaskSpec field-envelope checks.

The P2 composition root additionally validates every record against canonical
CONTRACTS/records.schema.json; this defensive local gate rejects unknown keys,
unsafe IDs, invalid lineage and malformed capability arrays before allocation.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
DIGEST=re.compile(r'^[a-f0-9]{64}$')

FEATURE_REQUIRED=frozenset({'project_id','feature_id','brief_id','original_brief_digest',
    'bible_pair_digest','revision','contract_digest','scope_digest','deliverables',
    'acceptance_ids','budget_limit_units','required_capabilities','unresolved_boundary_refs'})
FEATURE_OPTIONAL=frozenset({'feature_branch','repository_ref'})
GRAPH_FIELDS=frozenset({'graph_id','project_id','feature_id','revision',
    'bible_pair_digest','feature_contract_digest','graph_digest','tasks'})
TASK_FIELDS=frozenset({'project_id','feature_id','task_id','graph_revision','cabal_id',
    'bible_pair_digest','feature_contract_digest','dependencies','required_skills',
    'owned_resources','input_digests','outputs','acceptance_ids','budget_limit_units','task_digest'})


def identifier(v: Any) -> bool:
    return isinstance(v,str) and bool(ID.fullmatch(v))


def digest(v: Any) -> bool:
    return isinstance(v,str) and bool(DIGEST.fullmatch(v))


def array(v: Any, kind: str = 'id', *, min_length: int = 0) -> bool:
    fn=identifier if kind=='id' else digest
    return (isinstance(v,list) and min_length<=len(v)<=4096 and
        all(fn(x) for x in v) and len(set(v))==len(v))


def positive_integer(v: Any, minimum: int = 0) -> bool:
    return type(v) is int and minimum<=v<=2**60


def valid_records(feature: Any, graph: Any) -> bool:
    if (not isinstance(feature,Mapping) or not FEATURE_REQUIRED.issubset(feature) or
        set(feature)-FEATURE_REQUIRED-FEATURE_OPTIONAL or not isinstance(graph,Mapping) or
        set(graph)!=GRAPH_FIELDS):
        return False
    if (not all(identifier(feature[k]) for k in ('project_id','feature_id','brief_id')) or
        not all(digest(feature[k]) for k in ('original_brief_digest','bible_pair_digest','contract_digest','scope_digest')) or
        not positive_integer(feature['revision'],1) or
        not positive_integer(feature['budget_limit_units']) or
        not array(feature['acceptance_ids']) or not array(feature['required_capabilities']) or
        not array(feature['unresolved_boundary_refs']) or
        not isinstance(feature['deliverables'],list) or not feature['deliverables'] or
        any(not isinstance(x,Mapping) for x in feature['deliverables'])):
        return False
    if (not all(identifier(graph[k]) for k in ('graph_id','project_id','feature_id')) or
        not all(digest(graph[k]) for k in ('bible_pair_digest','feature_contract_digest','graph_digest')) or
        not positive_integer(graph['revision'],1) or not isinstance(graph['tasks'],list) or
        not 1<=len(graph['tasks'])<=4096 or
        any(graph[k]!=feature[k] for k in ('project_id','feature_id','bible_pair_digest')) or
        graph['feature_contract_digest']!=feature['contract_digest']):
        return False
    task_ids=set()
    for task in graph['tasks']:
        if (not isinstance(task,Mapping) or set(task)!=TASK_FIELDS or
            not all(identifier(task[k]) for k in ('project_id','feature_id','task_id','cabal_id')) or
            task['task_id'] in task_ids or
            not all(digest(task[k]) for k in ('bible_pair_digest','feature_contract_digest','task_digest')) or
            not positive_integer(task['graph_revision'],1) or not positive_integer(task['budget_limit_units']) or
            any(task[k]!=graph[k] for k in ('project_id','feature_id','bible_pair_digest')) or
            task['feature_contract_digest']!=graph['feature_contract_digest'] or
            task['graph_revision']!=graph['revision'] or
            any(not array(task[k],kind='digest' if k=='input_digests' else 'id')
                for k in ('dependencies','required_skills','owned_resources','input_digests','outputs','acceptance_ids'))):
            return False
        task_ids.add(task['task_id'])
    return True
