#!/usr/bin/env python3
"""R06 deterministic 174/12 manual-role successor export. No V1 launch paths."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'studio/v16/worker/ROLE_REGISTRY.v1.json'
DEPARTMENTS=ROOT/'studio/workforce/v4/departments.v4.json'
OUTPUT=ROOT/'studio/v16/worker/ROLE_REGISTRY_CURRENT_CANDIDATE.json'

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')

def create(roles_source,department_source):
    roles=roles_source['roles']
    departments=department_source['departments']
    if len(roles)!=174 or len(departments)!=12 or roles_source['role_count']!=174 or roles_source['department_count']!=12:
        raise ValueError('expected exactly 174 roles and 12 departments')
    ids=[row['role_id'] for row in roles]
    if len(set(ids))!=174:
        raise ValueError('duplicate role IDs')
    export_departments=[{'department_id':name,'display_name':detail['display_name'],
                         'head_role_id':detail['head_role']} for name,detail in sorted(departments.items())]
    export_roles=[]
    for role in sorted(roles,key=lambda x:x['role_id']):
        if (role['department'] not in departments or
            not role['manual_skill_uri'].startswith('skills://rpg-') or
            role['head_role_id'] not in ids or
            department_source['role_to_department'][role['role_id']]!=role['department']):
            raise ValueError('role home mismatch: '+role['role_id'])
        export_roles.append({'role_id':role['role_id'],'title':role['title'],
                             'department_id':role['department'],'head_role_id':role['head_role_id'],
                             'skill_uri':role['manual_skill_uri'],
                             'launch_mode':'HUMAN_OPENED_MANUAL_CHAT'})
    if any(dept['head_role_id'] not in ids for dept in export_departments):
        raise ValueError('department head missing')
    output={'schema':'NOMAD-ROLE-DEPARTMENT-CURRENT-CANDIDATE/1',
            'ratification':'UNRATIFIED','execution_authority':'NONE',
            'role_count':174,'department_count':12,
            'source_role_manifest_sha256':hashlib.sha256(canonical(roles_source)).hexdigest(),
            'source_department_manifest_sha256':hashlib.sha256(canonical(department_source)).hexdigest(),
            'departments':export_departments,'roles':export_roles}
    return canonical(output)+b'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check',action='store_true')
    p.add_argument('--output',type=Path,default=OUTPUT)
    args=p.parse_args()
    result=create(json.loads(SOURCE.read_text()),json.loads(DEPARTMENTS.read_text()))
    if args.check:
        if args.output.read_bytes()!=result:
            raise ValueError('role export drift')
    else:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_bytes(result)
    print(hashlib.sha256(result).hexdigest())
    return 0

if __name__=='__main__':
    raise SystemExit(main())
