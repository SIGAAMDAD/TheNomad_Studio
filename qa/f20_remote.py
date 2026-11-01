"""Independent GitHub readback for two addressed browser-shift receipts.

This verifies immutable remote command/receipt bytes, not the human observation
that the same browser response remained active. That evidence must be added by
the independent owner reviewer before F-20 can be accepted.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import json
import os
import re
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def _identity(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f'{name} must be nonempty')
    return value


def _path(value: str) -> str:
    _identity(value, 'GitHub evidence path')
    if value.startswith('/') or '\\' in value or any(
            item in ('', '.', '..') for item in value.split('/')):
        raise ValueError('unsafe GitHub evidence path')
    return value


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timezone required')
        return parsed
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('invalid receipt/command time') from exc


class GitHubReadback:
    """Uses the exact immutable commit SHA, never the mutable channel tip."""
    def __init__(self, repository: str, token: str):
        if not isinstance(repository, str) or not _REPO.fullmatch(repository):
            raise ValueError('invalid GitHub repository')
        if not isinstance(token, str) or not token:
            raise ValueError('authenticated GitHub token required')
        self.repository, self.token = repository, token

    def __call__(self, path: str, commit_sha: str) -> bytes:
        _path(path)
        if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha):
            raise ValueError('exact commit SHA required')
        url = ('https://api.github.com/repos/' + self.repository +
               '/contents/' + quote(path, safe='/') + '?' + urlencode({'ref':commit_sha}))
        request = Request(url, headers={'Accept': 'application/vnd.github+json',
                                        'Authorization': 'Bearer ' + self.token})
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or payload.get('type') != 'file' or payload.get('encoding') != 'base64':
            raise ValueError('remote GitHub object is not a base64 file')
        raw = base64.b64decode(payload['content'], validate=False)
        oid = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\x00' + raw).hexdigest()
        if payload.get('sha') != oid or payload.get('size') != len(raw):
            raise ValueError('GitHub blob readback identity mismatch')
        return raw


def verify_remote_pairs(evidence: dict, readback, *, trusted_scope: dict | None = None) -> dict:
    if not isinstance(evidence, dict):
        raise ValueError('evidence must be a record')
    if trusted_scope is not None:
        required = ('repository', 'project_id', 'channel_id', 'shift_id', 'contract_digest')
        if any(not trusted_scope.get(key) or evidence.get(key) != trusted_scope[key]
               for key in required):
            raise ValueError('remote evidence differs from trusted deployment scope')
    project = _identity(evidence.get('project_id'), 'project_id')
    channel = _identity(evidence.get('channel_id'), 'channel_id')
    shift = _identity(evidence.get('shift_id'), 'shift_id')
    contract = evidence.get('contract_digest')
    if not isinstance(contract, str) or not _HEX.fullmatch(contract):
        raise ValueError('exact contract digest required')
    pairs = evidence.get('pairs')
    if not isinstance(pairs, list) or len(pairs) != 2:
        raise ValueError('exactly two command/receipt pairs required')
    seen_messages, seen_receipts, seen_tasks, seen_receipt_commits = set(), set(), set(), set()
    moments = []
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {
                'command_path','command_commit_sha','receipt_path','receipt_commit_sha'}:
            raise ValueError('incomplete remote evidence locator')
        for key in ('command_commit_sha', 'receipt_commit_sha'):
            if not isinstance(pair[key], str) or not _SHA.fullmatch(pair[key]):
                raise ValueError('exact GitHub commit identity required')
        command_raw = readback(_path(pair['command_path']), pair['command_commit_sha'])
        receipt_raw = readback(_path(pair['receipt_path']), pair['receipt_commit_sha'])
        try:
            command, receipt = json.loads(command_raw), json.loads(receipt_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError('remote evidence is not valid JSON') from exc
        if not isinstance(command, dict) or not isinstance(receipt, dict):
            raise ValueError('remote command/receipt must be records')
        if (command.get('protocol_version') != '1' or command.get('kind') != 'ASSIGN'
                or command.get('project_id') != project
                or command.get('channel_id') != channel
                or command.get('to_shift_id') != shift
                or command.get('contract_digest') != contract
                or type(command.get('lease_generation')) is not int
                or command['lease_generation'] < 1):
            raise ValueError('remote assignment identity differs from the same shift')
        if (receipt.get('protocol_version') != '1'
                or receipt.get('kind') not in ('SEEN','CHECKPOINT','SUBMITTED')
                or receipt.get('project_id') != project
                or receipt.get('channel_id') != channel
                or receipt.get('recipient_shift_id') != shift
                or receipt.get('contract_digest') != contract
                or receipt.get('message_id') != command.get('message_id')
                or receipt.get('observed_message_sha256') != hashlib.sha256(command_raw).hexdigest()):
            raise ValueError('receipt does not authenticate the addressed message bytes')
        message_id = _identity(command.get('message_id'), 'message_id')
        receipt_id = _identity(receipt.get('receipt_id'), 'receipt_id')
        task_id = _identity(command.get('task_id'), 'task_id')
        if (message_id in seen_messages or receipt_id in seen_receipts or
                task_id in seen_tasks or pair['receipt_commit_sha'] in seen_receipt_commits):
            raise ValueError('duplicate messages, tasks, receipts or receipt commits')
        seen_messages.add(message_id)
        seen_receipts.add(receipt_id)
        seen_tasks.add(task_id)
        seen_receipt_commits.add(pair['receipt_commit_sha'])
        issued = _timestamp(command.get('issued_at'))
        observed = _timestamp(receipt.get('issued_at'))
        if observed < issued:
            raise ValueError('receipt predates its command')
        moments.append((issued, observed))
    if moments[1][0] < moments[0][1]:
        raise ValueError('second command must follow the first remote receipt')
    return {'result':'TWO_REMOTE_RECEIPTS_VERIFIED', 'project_id':project,
            'channel_id':channel,'shift_id':shift,'distinct_receipts':2,
            'browser_continuity':'REQUIRES_INDEPENDENT_OBSERVATION'}


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--evidence', required=True)
    cli.add_argument('--token-env', default='STUDIO_GITHUB_TOKEN')
    args = cli.parse_args()
    with open(args.evidence, encoding='utf-8') as evidence_file:
        source = json.load(evidence_file)
    # These values come from the independent operator's trusted configuration,
    # never from the evidence JSON or an unverified GitHub receipt.
    scope = {key:os.environ.get('STUDIO_F20_'+key.upper(), '')
             for key in ('repository','project_id','channel_id','shift_id','contract_digest')}
    token = os.environ.get(args.token_env, '')
    result = verify_remote_pairs(source,
        GitHubReadback(scope['repository'], token), trusted_scope=scope)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
