"""WG-L repeatable evidence runner; no unrun gate is silently counted PASS.

Usage: python -m qa.runner --packet /path/to/plan --out /tmp/l-report
       [--candidate /path/to/merged/tree --submissions /path/to/submissions]
       [--role-inventory /path/to/roles.json]
       [--scenarios /path/to/real-scenarios.json --allow-commands]
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import platform
import subprocess
import sys
from .core import atomic_json, digest, read_json, result, source_files, tree_digest
from .contract import EXPECTED_PLAN_SHA, load_plan, WORKGROUPS, pinned_contract_digests
from .scanning import scan
from .submissions import verify_all
from .roles import verify_roles
from .runtime import read_scenarios, run_scenario
from .receipts import validate_review
from .apsw_probe import run as apsw_probe
from .blackbox import project_isolation_probe
from .sql_policy_probe import run as sql_policy_probe

FAULTS = ('blob-stage', 'sql-commit', 'provider-timeout', 'host-kill', 'github-write', 'promotion')
REF_COMMANDS = [
    ('L-A-REFERENCE-SQL', 'AUDIT/test_sql_reference.py'),
    ('L-A-CONTRACTS', 'AUDIT/test_contracts.py'),
    ('L-A-RELAYS', 'AUDIT/test_relay_contract.py'),
    ('L-A-NO-PAID-INFERENCE', 'AUDIT/test_no_paid_inference.py'),
    ('L-A-GATE-ALIGNMENT', 'AUDIT/test_r5_gate_alignment.py'),
    ('L-A-FORGE-ALIGNMENT', 'tests/test_forge_alignment.py'),
    ('L-A-NO-ARTIFICIAL-BLOCK', 'tests/test_no_artificial_blocks.py'),
    ('L-A-DISPATCH-INVARIANTS', 'tests/test_dispatch_invariants.py'),
]


def _safe_case_name(text: str) -> str:
    return ''.join(c if c.isalnum() or c in '_-' else '_' for c in text)[:120]


def reference_test(packet: Path, test_id: str, script: str, logs: Path):
    if not (packet / script).exists():
        return result(test_id, 'A', 'NOT_RUN', f'missing packet reference test: {script}')
    command = [sys.executable, str(packet / script)]
    try:
        run = subprocess.run(command, cwd=packet, capture_output=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return result(test_id, 'A', 'FAIL', f'reference test failed to execute: {exc}', command=command)
    log = logs / f'{_safe_case_name(test_id)}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b'$ ' + ' '.join(command).encode('utf8') + b'\nEXIT: ' + str(run.returncode).encode() + b'\nSTDOUT:\n' + run.stdout + b'\nSTDERR:\n' + run.stderr)
    return result(test_id, 'A', 'PASS' if run.returncode == 0 else 'FAIL',
                  f'reference test {script}, exit {run.returncode}', command=command,
                  exit_code=run.returncode, log=str(log), log_sha256=digest(log.read_bytes()))


def run(packet: Path, out: Path, candidate: Path | None = None, submissions: Path | None = None,
        inventory: Path | None = None, scenarios: Path | None = None, allow_commands: bool = False,
        review: Path | None = None, artifacts: Path | None = None,
        isolation_config: Path | None = None, role_root: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    results = []
    plan = None
    try:
        plan = load_plan(packet)
        results.append(result('L-A-PLAN-FROZEN', 'A', 'PASS', 'exact SHA and 13 owned groups in one wave',
                              plan_sha256=EXPECTED_PLAN_SHA, workgroups=list(WORKGROUPS), waves=1))
        contract_hashes = pinned_contract_digests(packet)
        results.append(result('L-A-FROZEN-CONTRACTS', 'A', 'PASS', 'read actual contract bytes',
                              paths_to_sha256=contract_hashes))
    except Exception as exc:
        results.append(result('L-A-PLAN-FROZEN', 'A', 'FAIL', str(exc), packet=str(packet)))
    if plan is not None:
        for test_id, script in REF_COMMANDS:
            results.append(reference_test(packet, test_id, script, out / 'logs'))
        try:
            probe = apsw_probe(packet / 'CONTRACTS/initial_schema.sql')
            results.append(result('L-A-APSW-SQL-REFERENCE', 'A', 'PASS',
                                  'actual APSW memory DB reference; no Studio service involved', **probe))
        except FileNotFoundError as exc:
            results.append(result('L-A-APSW-SQL-REFERENCE', 'A', 'NOT_RUN', str(exc)))
        except Exception as exc:
            results.append(result('L-A-APSW-SQL-REFERENCE', 'A', 'FAIL', str(exc)))
        try:
            probe = sql_policy_probe(packet / 'CONTRACTS/initial_schema.sql', packet / 'tests/sql_policy_cases.json')
            results.append(result('L-A-SQL-POLICY-CASES', 'A', 'PASS' if probe['passed'] else 'FAIL',
                                  'actual fresh in-memory SQLite schema and frozen positive/negative probes', **probe))
        except Exception as exc:
            results.append(result('L-A-SQL-POLICY-CASES', 'A', 'FAIL', str(exc)))
        matrix = read_json(packet / 'QA/ACCEPTANCE_MATRIX.json')
        actual = {r.test_id:r for r in results}
        frozen_offline = {
            'TEST-R5-SQL-REFERENCE': 'L-A-REFERENCE-SQL',
            'TEST-FORGE-OWNERSHIP': 'L-A-FORGE-ALIGNMENT',
            'TEST-FORGE-SQL-PROBE': 'L-A-SQL-POLICY-CASES',
            'TEST-R5-API-CONTRACT': 'L-A-CONTRACTS',
            'TEST-NO-ARTIFICIAL-BLOCK': 'L-A-NO-ARTIFICIAL-BLOCK',
        }
        for test_id in sorted({t['id'] for t in matrix['tests']}):
            if test_id in frozen_offline:
                ref = actual.get(frozen_offline[test_id])
                if ref is None:
                    results.append(result(test_id, 'A', 'NOT_RUN', 'linked frozen reference case unavailable'))
                elif ref.status == 'PASS':
                    results.append(result(test_id, 'A', 'PASS', 'exact linked offline reference check executed',
                                          executed_test_id=ref.test_id, log_or_evidence=ref.evidence))
                else:
                    results.append(result(test_id, 'A', ref.status, ref.detail))
            else:
                results.append(result(test_id, 'B', 'NOT_RUN',
                                      'Full integrated acceptance requires assembled production source, independent QA and actual evidence'))


    candidate_hash = None
    if candidate is None or not candidate.is_dir():
        results.append(result('L-A-CANDIDATE-BYTES', 'A', 'NOT_RUN', 'canonical merged source tree absent'))
        results.append(result('L-A-NEGATIVE-SOURCE-SCAN', 'A', 'NOT_RUN', 'no source to scan'))
    else:
        try:
            files = source_files(candidate)
            candidate_hash = tree_digest(files)
            results.append(result('L-A-CANDIDATE-BYTES', 'A', 'PASS' if files else 'NOT_RUN', 'full canonical file hash map observed' if files else 'empty candidate tree',
                                  candidate_tree_sha256=candidate_hash, file_count=len(files), files=files))
            if files:
                hits = scan(candidate)
                results.append(result('L-A-NEGATIVE-SOURCE-SCAN', 'A', 'FAIL' if hits else 'PASS',
                                      'static forbidden-pattern scan; not proof of no hidden API calls',
                                      candidate_tree_sha256=candidate_hash, scanned_file_count=len(files), findings=hits))
            else:
                results.append(result('L-A-NEGATIVE-SOURCE-SCAN', 'A', 'NOT_RUN', 'empty candidate tree'))
        except Exception as exc:
            results.append(result('L-A-CANDIDATE-BYTES', 'A', 'FAIL', str(exc), candidate=str(candidate)))

    if plan is not None and candidate is not None and submissions is not None:
        try:
            verified = verify_all(packet, plan, submissions, candidate)
            results.append(result('L-A-ALL-13-SUBMISSIONS', 'A', 'PASS', 'all 13 source archives and logs byte-verified against merged tree', **verified))
        except FileNotFoundError as exc:
            results.append(result('L-A-ALL-13-SUBMISSIONS', 'A', 'NOT_RUN', str(exc)))
        except Exception as exc:
            results.append(result('L-A-ALL-13-SUBMISSIONS', 'A', 'FAIL', str(exc)))
    else:
        results.append(result('L-A-ALL-13-SUBMISSIONS', 'A', 'NOT_RUN', 'needs canonical source and 13 submission sidecars'))

    installed_root = role_root if role_root is not None else candidate
    if installed_root is not None and inventory is not None:
        try:
            roles = verify_roles(installed_root, inventory)
            status = 'FAIL' if roles['defects'] else ('PASS' if roles['ready']==174 and roles['missing_count']==0 else 'NOT_RUN')
            results.append(result('L-A-174-ROLE-BYTES', 'A', status,
                                  'real role/resource bytes and smoke logs, no synthetic role entries', **roles))
        except FileNotFoundError as exc:
            results.append(result('L-A-174-ROLE-BYTES', 'A', 'NOT_RUN', str(exc)))
        except Exception as exc:
            results.append(result('L-A-174-ROLE-BYTES', 'A', 'FAIL', str(exc)))
    else:
        results.append(result('L-A-174-ROLE-BYTES', 'A', 'NOT_RUN', 'source and installed role manifest not supplied',
                              target=174, actual_verified=0, unavailable=174))

    if review is not None and candidate_hash is not None and artifacts is not None:
        try:
            verified = validate_review(candidate_hash, review, artifacts)
            results.append(result('L-A-QA-RECEIPT-CONSISTENCY', 'A', 'PASS',
                                  'byte and independence checks only; authenticated E/remote verification remains Tier B', **verified))
        except Exception as exc:
            results.append(result('L-A-QA-RECEIPT-CONSISTENCY', 'A', 'FAIL', str(exc)))
    else:
        results.append(result('L-A-QA-RECEIPT-CONSISTENCY', 'A', 'NOT_RUN', 'no source-bound independent reviewer receipts'))

    if isolation_config is None or candidate_hash is None:
        results.append(result('L-B-TWO-PROJECT-ISOLATION', 'B', 'NOT_RUN', 'two configured real scoped principals and integrated API missing'))
    else:
        try:
            cfg = read_json(isolation_config)
            if cfg.get('candidate_tree_sha256') != candidate_hash:
                raise ValueError('isolation probe configured for wrong candidate digest')
            observed = project_isolation_probe(cfg)
            results.append(result('L-B-TWO-PROJECT-ISOLATION', 'B', 'PASS' if observed['success'] else 'FAIL',
                                  'actual two-project positive/negative authorization HTTP requests', **observed,
                                  candidate_tree_sha256=candidate_hash))
        except (FileNotFoundError, PermissionError) as exc:
            results.append(result('L-B-TWO-PROJECT-ISOLATION', 'B', 'NOT_RUN', str(exc)))
        except Exception as exc:
            results.append(result('L-B-TWO-PROJECT-ISOLATION', 'B', 'FAIL', str(exc)))

    scenario_map = {}
    if scenarios is not None:
        try:
            scenario_map = read_scenarios(scenarios)
        except Exception as exc:
            results.append(result('L-B-SCENARIO-INPUT', 'B', 'FAIL', str(exc)))
    if candidate_hash is None:
        results.append(result('L-B-SCENARIOS', 'B', 'NOT_RUN', 'merged source bytes absent'))
    elif not scenario_map:
        results.append(result('L-B-SCENARIOS', 'B', 'NOT_RUN', 'no configured real HTTP/host/CI scenarios'))
    else:
        for case_id, case in sorted(scenario_map.items()):
            try:
                expected = case.get('candidate_tree_sha256')
                if expected != candidate_hash:
                    raise ValueError('scenario is not bound to actual candidate tree digest')
                observed = run_scenario(case, allow_commands)
                results.append(result(f'L-B-{case_id}', 'B', observed['status'],
                                      'real declared HTTP/process/artifact steps executed', **observed,
                                      candidate_tree_sha256=candidate_hash))
            except (FileNotFoundError, PermissionError) as exc:
                results.append(result(f'L-B-{case_id}', 'B', 'NOT_RUN', str(exc)))
            except Exception as exc:
                results.append(result(f'L-B-{case_id}', 'B', 'FAIL', str(exc)))
    for phase in FAULTS:
        key = f'fault-{phase}'
        if key not in scenario_map or candidate_hash is None:
            results.append(result(f'L-B-FAULT-{phase}', 'B', 'NOT_RUN', 'real configured failpoint/restart/readback absent'))
        else:
            case = scenario_map[key]
            checked = next((r for r in results if r.test_id == f'L-B-{key}'), None)
            steps = case.get('steps', [])
            valid = (len(steps) >= 3 and steps[0].get('type') == 'http'
                     and steps[-1].get('type') == 'http'
                     and steps[-1].get('expect_same_body_as_step') == 0
                     and any(s.get('type') in ('http', 'process') for s in steps[1:-1]))
            if not valid:
                results.append(result(f'L-B-FAULT-{phase}', 'B', 'FAIL', 'crash phase must probe state before, inject a failure, and verify identical state afterward'))
            else:
                results.append(result(f'L-B-FAULT-{phase}', 'B', checked.status,
                                      'failure-injection scenario outcome; real configured codepath required',
                                      scenario_id=key, scenario_status=checked.status,
                                      candidate_tree_sha256=candidate_hash)) if checked.status != 'NOT_RUN' else results.append(result(f'L-B-FAULT-{phase}', 'B', 'NOT_RUN', checked.detail))
    results.append(result('L-C-MANUAL-BROWSER-PILOT', 'C', 'NOT_RUN',
                          'requires witnessed two human-opened browser sessions, actual GitHub readbacks, STOP/no-wake, manual restart, real CI and independent QA'))
    evidence = {
        'format_version': 1, 'producer': 'WG-L', 'source_plan_sha256': EXPECTED_PLAN_SHA,
        'candidate_tree_sha256': candidate_hash, 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'python': sys.version.split()[0], 'platform': platform.platform(),
        'results': [r.record() for r in results],
        'counts': {s: sum(r.status == s for r in results) for s in ('PASS', 'FAIL', 'NOT_RUN')},
        'interpretation': 'Tier A reference/byte checks do not imply Tier B studio/runtime or Tier C live ChatGPT/browser/provider completion'
    }
    atomic_json(out / 'results.json', evidence)
    summary = '\n'.join(f'{r.status:7} {r.tier} {r.test_id}: {r.detail}' for r in results)
    (out / 'results.txt').write_text(summary + '\n', encoding='utf-8')
    return evidence


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--packet', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--candidate', type=Path)
    ap.add_argument('--submissions', type=Path)
    ap.add_argument('--role-inventory', type=Path)
    ap.add_argument('--role-root', type=Path)
    ap.add_argument('--scenarios', type=Path)
    ap.add_argument('--review', type=Path)
    ap.add_argument('--review-artifacts', type=Path)
    ap.add_argument('--isolation-config', type=Path)
    ap.add_argument('--allow-commands', action='store_true')
    ns = ap.parse_args(argv)
    doc = run(ns.packet, ns.out, ns.candidate, ns.submissions, ns.role_inventory,
              ns.scenarios, ns.allow_commands, ns.review, ns.review_artifacts, ns.isolation_config, ns.role_root)
    print('WG-L evidence:', ns.out / 'results.json', 'counts:', doc['counts'])
    return int(doc['counts']['FAIL'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
