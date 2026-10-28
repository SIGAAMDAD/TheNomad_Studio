#!/usr/bin/env python3
"""R07 release sweep: fail strict mode for any live compatibility artifact."""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
DEAD_PREFIXES=('studio/v1/','studio/architecture/hyperscale/v1/',
               'studio/workforce/v4/','infra/db/','studio_v16/db/migrations/')
DEAD_FILES={'studio_v16/db/store.py','studio_v16/db/publication.py',
            'studio_v16/db/migrate.py','studio_v16/adapters/runtime.py',
            'scripts/studio.py','scripts/production.py','scripts/board_sync.py',
            'scripts/studio_comms.py','scripts/studio_inbox.py'}
DEAD_WORKFLOWS={'.github/workflows/studio-board-sync.yml',
                '.github/workflows/studio-reconcile.yml',
                '.github/workflows/studio-comms-broker.yml',
                '.github/workflows/studio-director-pulse.yml',
                '.github/workflows/studio-ci-evidence-relay.yml',
                '.github/workflows/studio-event-route.yml',
                '.github/workflows/ai-draft.yml',
                '.github/workflows/ai-manual.yml'}
DEAD_REFS=(r'PostgresStore',r'shadow_legacy',r'POSTGRES_ACTIVE',r'POSTGRES_SHADOW',
           r'observe_legacy_runtime',r'observe_archived_events',r'work_event_router',
           r'studio/runtime',r'studio/comms',r'studio/v1/prompts/')
EXCLUDED=('tests/','docs/','architecture/inventory/','studio/v16/contracts/',
          'studio/v16/worker/ROLE_REGISTRY_CURRENT_CANDIDATE.json',
          'scripts/legacy_retirement_gate.py')

def active(path):
    return path.endswith(('.py','.yml','.yaml','.json','.md','.toml','.ini')) and not path.startswith(EXCLUDED)

def inspect_files(root,paths):
    issues=[]
    for path in sorted(set(paths)):
        if path.startswith(DEAD_PREFIXES) or path in DEAD_FILES or path in DEAD_WORKFLOWS:
            issues.append({'code':'LEGACY_PATH_PRESENT','path':path})
        if not active(path):
            continue
        text=(root/path).read_text(encoding='utf-8',errors='replace')
        for pattern in DEAD_REFS:
            if re.search(pattern,text):
                issues.append({'code':'LEGACY_RUNTIME_REFERENCE','path':path,'pattern':pattern})
        if path.endswith('.py'):
            try:
                astree=ast.parse(text,filename=path)
            except SyntaxError:
                issues.append({'code':'UNPARSABLE_PYTHON','path':path})
                continue
            for node in ast.walk(astree):
                if isinstance(node,ast.Import):
                    names=[x.name for x in node.names]
                elif isinstance(node,ast.ImportFrom):
                    names=[node.module or '']
                else:
                    continue
                for name in names:
                    if name.startswith(('studio_v16.db.store','studio_v16.db.publication',
                                         'studio_v16.db.migrate','studio_v16.adapters.runtime')):
                        issues.append({'code':'RETIRED_MODULE_IMPORT','path':path,'module':name})
    return issues

def tracked(root):
    return subprocess.check_output(['git','-C',str(root),'ls-tree','-r','--name-only','HEAD'],
                                   text=True).splitlines()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--strict',action='store_true')
    parser.add_argument('--json',type=Path)
    args=parser.parse_args()
    root=args.root.resolve()
    issues=inspect_files(root,tracked(root))
    result={'schema':'NOMAD-R07-RETIREMENT-SWEEP/1',
            'verdict':'BLOCKED' if issues else 'SOURCE_CLEAN',
            'strict':args.strict,'issue_count':len(issues),'issues':issues,
            'deployment_verification':'NOT_ASSERTED','owner_deletion_approval':'NOT_ASSERTED'}
    output=json.dumps(result,sort_keys=True,indent=2)+'\n'
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(output)
    print(output)
    return 1 if args.strict and issues else 0

if __name__=='__main__':
    raise SystemExit(main())
