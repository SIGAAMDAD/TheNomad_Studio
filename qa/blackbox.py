"""Fixed black-box multi-project service probes tied to frozen OpenAPI routes."""
from __future__ import annotations
import re
from urllib.parse import quote
from .runtime import execute_step, RuntimeErrorQA

_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')


def _id(s: str) -> str:
    if not isinstance(s, str) or not _ID.fullmatch(s):
        raise ValueError('project identifier is unsafe')
    return quote(s, safe='')


def project_isolation_probe(config: dict) -> dict:
    """Checks actual scoped operator credentials for two existing fixture projects.

    NEVER silently use a single owner token, whose broad access would invalidate
    the test. The two principals must be project-scoped by test setup.
    """
    base = config.get('base_url', '').rstrip('/')
    if not base.startswith(('http://', 'https://')):
        raise ValueError('configured API origin required')
    a, b = _id(config['project_a']), _id(config['project_b'])
    if a == b or config.get('token_a_env') == config.get('token_b_env'):
        raise ValueError('two distinct projects and scoped principals required')
    checks = [
        (a, config['token_a_env'], [200], 'A->A'),
        (b, config['token_a_env'], [401, 403, 404], 'A->B'),
        (b, config['token_b_env'], [200], 'B->B'),
        (a, config['token_b_env'], [401, 403, 404], 'B->A'),
    ]
    evidence = []
    for pid, token_env, statuses, label in checks:
        obs = execute_step({'type':'http', 'method':'GET', 'url':f'{base}/v1/projects/{pid}',
                            'headers_from_env':{'Authorization':token_env},
                            'expect_status':statuses})
        obs['step'] = label
        evidence.append(obs)
        if not obs['success']:
            break
    return {'success': len(evidence)==4 and all(o['success'] for o in evidence),
            'observations':evidence, 'feature':'two-project REST access fence'}
