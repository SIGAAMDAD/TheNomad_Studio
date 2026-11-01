#!/usr/bin/env python3
"""Check the real HTTPS operator endpoint without mutating Studio state."""
from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def probe_operator(url: str, token: str, *, opener=urlopen) -> int:
    address = urlsplit(url)
    if address.scheme != 'https' or not address.hostname or address.username or address.password:
        raise ValueError('operator endpoint must be an HTTPS URL without credentials')
    if address.path not in ('', '/') or address.query or address.fragment:
        raise ValueError('specify the HTTPS origin only')
    if not isinstance(token, str) or len(token) < 16:
        raise ValueError('trusted operator token required')
    target = url.rstrip('/') + '/v1/projects'
    try:
        response = opener(Request(target, headers={'Accept': 'application/json'}), timeout=10)
    except HTTPError as exc:
        if exc.code != 401:
            raise ValueError(f'unauthenticated request returned HTTP {exc.code}') from exc
    else:
        with response:
            raise ValueError('unauthenticated operator request was allowed')
    with opener(Request(target, headers={'Authorization': 'Bearer '+token,
                                         'Accept': 'application/json'}), timeout=10) as response:
        if response.status != 200:
            raise ValueError(f'authenticated request returned HTTP {response.status}')
        payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            raise ValueError('operator returned an invalid projects envelope')
        if any(not isinstance(p, dict) or not isinstance(p.get('project_id'), str)
               for p in payload['items']):
            raise ValueError('operator returned invalid project records')
        return len(payload['items'])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True, help='HTTPS origin of the deployed cockpit')
    p.add_argument('--token-env', default='STUDIO_OPERATOR_TOKEN',
                   help='environment variable containing the private operator token')
    opts = p.parse_args()
    count = probe_operator(opts.url, os.environ.get(opts.token_env, ''))
    print(f'Verified authenticated Studio operator API: projects={count}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
