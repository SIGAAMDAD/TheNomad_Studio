#!/usr/bin/env python3
"""Real host TLS/mTLS probe. NOT proof of an ordinary ChatGPT web-chat connector."""
import argparse
import json
import secrets
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _verified_open(request, *, context, timeout):
    client = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect())
    return client.open(request, timeout=timeout)


def run(argv=None, *, opener=None, challenge_factory=None):
    parser = argparse.ArgumentParser(description='Test gateway network TLS and challenge echo; not release acceptance')
    parser.add_argument('--url', required=True, help='Public HTTPS origin, e.g. https://bridge.example.org')
    parser.add_argument('--ca-file', required=True)
    parser.add_argument('--client-cert', required=True)
    parser.add_argument('--client-key', required=True)
    parser.add_argument('--path', default='/v1/connector/probe')
    args = parser.parse_args(argv)
    try:
        u = urlsplit(args.url)
        _port = u.port
    except ValueError:
        print(json.dumps({'state': 'BLOCKED', 'code': 'INVALID_SCHEMA'}))
        return 2
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('', '/'):
        print(json.dumps({'state': 'BLOCKED', 'code': 'INVALID_SCHEMA'}))
        return 2
    if not args.path.startswith('/') or args.path.startswith('//') or '?' in args.path or '#' in args.path:
        print(json.dumps({'state': 'BLOCKED', 'code': 'INVALID_SCHEMA'}))
        return 2
    challenge = (challenge_factory or (lambda: secrets.token_hex(32)))()
    try:
        context = ssl.create_default_context(cafile=args.ca_file)
        context.load_cert_chain(args.client_cert, args.client_key)
    except (OSError, ssl.SSLError, ValueError):
        print(json.dumps({'state': 'BLOCKED', 'code': 'NOT_CONFIGURED', 'real_chat_verified': False}))
        return 2
    req = urllib.request.Request(args.url.rstrip('/') + args.path,
        data=json.dumps({'version': 'NOMAD/QUERY/1', 'challenge': challenge}, separators=(',', ':')).encode(),
        method='POST', headers={'Content-Type': 'application/json'})
    try:
        with (opener or _verified_open)(req, context=context, timeout=10) as response:
            if response.status != 200:
                raise ValueError('bad status')
            body = response.read(4097)
            if len(body) > 4096:
                raise ValueError('oversized reply')
            def unique_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('duplicate reply key')
                    result[key] = value
                return result
            result = json.loads(body, object_pairs_hook=unique_pairs)
            if set(result) != {'version', 'challenge', 'authenticated'} or result['version'] != 'NOMAD/RECEIPT/1' or result['challenge'] != challenge or result['authenticated'] is not True:
                raise ValueError('invalid receipt')
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        print(json.dumps({'state': 'UNKNOWN', 'code': 'PROVIDER_UNKNOWN', 'real_chat_verified': False}))
        return 1
    print(json.dumps({'state': 'UNKNOWN', 'code': 'HOST_TLS_PROBE_OK_CHAT_UNVERIFIED', 'real_chat_verified': False}))
    return 0


if __name__ == '__main__':
    sys.exit(run())
