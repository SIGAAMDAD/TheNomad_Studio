"""Evidence-producing real HTTP and process acceptance scenarios.

A scenario passes only when *actual* requests/processes were invoked and their
specified results observed. No mocked network or preloaded success receipts.
An operator-provided scenario configuration is untrusted and conveys no owner
permissions; it is only a description of local test commands.
"""
from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
import json
import os
import subprocess
import time
from .core import digest, read_json


class RuntimeErrorQA(ValueError):
    pass


def _json_match(payload, dot_path: str, expected) -> bool:
    obj = payload
    for key in dot_path.split('.'):
        if not isinstance(obj, dict) or key not in obj:
            return False
        obj = obj[key]
    return obj == expected


def execute_step(step: dict, allow_commands: bool = False) -> dict:
    """Never log authorization headers, env secret values or response bodies."""
    kind = step.get('type')
    started = time.monotonic()
    if kind == 'http':
        endpoint = step.get('url', '')
        if not (endpoint.startswith('http://') or endpoint.startswith('https://')):
            raise RuntimeErrorQA('HTTP test requires absolute configured URL')
        method = step.get('method', 'GET').upper()
        if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}:
            raise RuntimeErrorQA('HTTP method outside contract')
        data = json.dumps(step['json']).encode() if 'json' in step else None
        headers = {'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        for name, env_name in step.get('headers_from_env', {}).items():
            secret = os.getenv(env_name)
            if not secret:
                raise FileNotFoundError(f'unset env credential: {env_name}')
            headers[name] = secret
        request = Request(endpoint, method=method, data=data, headers=headers)
        try:
            with urlopen(request, timeout=min(45, int(step.get('timeout_seconds', 15)))) as response:
                status = response.status
                response_bytes = response.read(2 * 1024 * 1024 + 1)
        except HTTPError as error:
            status = error.code
            response_bytes = error.read(2 * 1024 * 1024 + 1)
        if len(response_bytes) > 2 * 1024 * 1024:
            raise RuntimeErrorQA('response exceeds test evidence limit')
        expected = step.get('expect_status')
        if not isinstance(expected, list) or not expected or not all(isinstance(n, int) for n in expected):
            raise RuntimeErrorQA('HTTP step requires explicit expected status list')
        success = status in expected
        equals = step.get('expect_json_equals', {})
        if equals:
            try:
                body = json.loads(response_bytes)
            except (ValueError, UnicodeDecodeError):
                body = None
            success = success and all(_json_match(body, k, v) for k, v in equals.items())
        parsed = urlsplit(endpoint)
        redacted_endpoint = f'{parsed.scheme}://{parsed.hostname or "redacted"}{parsed.path}'
        return {'type': 'http', 'method': method, 'endpoint': redacted_endpoint,
                'status': status, 'expected_status': expected, 'body_sha256': digest(response_bytes),
                'success': success, 'elapsed_ms': round((time.monotonic()-started)*1000, 2)}
    if kind == 'process':
        if not allow_commands:
            raise PermissionError('process acceptance execution requires --allow-commands')
        argv = step.get('argv')
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise RuntimeErrorQA('process step requires fixed argv; shell is forbidden')
        expected = step.get('expect_exit')
        if not isinstance(expected, list) or not expected or not all(isinstance(x, int) for x in expected):
            raise RuntimeErrorQA('process step must declare expected exit codes')
        cwd = step.get('cwd')
        if cwd is not None and not Path(cwd).is_dir():
            raise FileNotFoundError(cwd)
        run = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=min(180, int(step.get('timeout_seconds', 30))), shell=False)
        return {'type': 'process', 'argv': argv, 'cwd': cwd, 'exit_code': run.returncode,
                'expected_exit': expected, 'stdout_sha256': digest(run.stdout), 'stderr_sha256': digest(run.stderr),
                'success': run.returncode in expected, 'elapsed_ms': round((time.monotonic()-started)*1000, 2)}
    if kind == 'artifact':
        path = Path(step['path'])
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(path)
        observed_sha = digest(path.read_bytes())
        expected_sha = step.get('sha256')
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise RuntimeErrorQA('artifact requires exact expected SHA256')
        return {'type': 'artifact', 'path': str(path), 'sha256': observed_sha,
                'success': observed_sha == expected_sha, 'elapsed_ms': round((time.monotonic()-started)*1000, 2)}
    raise RuntimeErrorQA(f'unsupported real step type: {kind}')


def run_scenario(case: dict, allow_commands: bool = False) -> dict:
    steps = case.get('steps', [])
    if not steps:
        raise RuntimeErrorQA('empty scenario cannot pass')
    observations = []
    for step in steps:
        observation = execute_step(step, allow_commands)
        for relation, equal in (('expect_same_body_as_step', True),
                                ('expect_different_body_from_step', False)):
            if relation in step:
                idx = step[relation]
                if not isinstance(idx, int) or idx < 0 or idx >= len(observations):
                    raise RuntimeErrorQA(f'{relation} must reference an earlier step index')
                previous = observations[idx]
                if previous.get('type') != 'http' or observation.get('type') != 'http':
                    raise RuntimeErrorQA('body comparison requires two actual HTTP observations')
                matches = observation['body_sha256'] == previous['body_sha256']
                observation[relation] = idx
                observation['success'] = observation['success'] and matches == equal
        observations.append(observation)
        if not observation['success']:
            break
    return {'scenario_id': case.get('id'), 'status': 'PASS' if len(observations)==len(steps) and all(x['success'] for x in observations) else 'FAIL',
            'steps_attempted': len(observations), 'steps_total': len(steps), 'observations': observations}


def read_scenarios(path: Path) -> dict[str, dict]:
    doc = read_json(path)
    if doc.get('format_version') != 1 or not isinstance(doc.get('scenarios'), list):
        raise RuntimeErrorQA('bad scenario document')
    by_id = {}
    for case in doc['scenarios']:
        case_id = case.get('id')
        if not isinstance(case_id, str) or not case_id or case_id in by_id:
            raise RuntimeErrorQA('scenario ID missing or duplicated')
        by_id[case_id] = case
    return by_id
