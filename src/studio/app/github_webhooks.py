"""Authenticated GitHub webhook hints with C-durable delivery deduplication.

A webhook cannot authorize a worker, complete a command, or mark CI passed.
It records an authenticated hint and signals independently verified G readback.
Missed webhook requests are handled by periodic reconciliation already running.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping

from studio.storage.workforce import WorkforceStorage
from studio.workforce import WorkforceFault


@dataclass(frozen=True)
class GitHubWebhookBinding:
    project_id: str
    owner: str
    repository: str
    source_ref: str
    communications_ref: str
    secret: bytes

    def __post_init__(self):
        fields = (self.project_id, self.owner, self.repository,
                  self.source_ref, self.communications_ref)
        if any(not isinstance(value, str) or not value or len(value) > 512
               for value in fields):
            raise ValueError('invalid authenticated GitHub webhook binding')
        if not isinstance(self.secret, bytes) or len(self.secret) < 16:
            raise ValueError('webhook binding needs a trusted signing secret')

    @property
    def full_name(self) -> str:
        return self.owner.lower() + '/' + self.repository.lower()

    @staticmethod
    def _branch(ref: str) -> str:
        return ref if ref.startswith('refs/heads/') else 'refs/heads/' + ref

    def accepts(self, event: str, payload: dict) -> bool:
        repository = payload.get('repository')
        if not isinstance(repository, dict) or not isinstance(repository.get('full_name'), str):
            return False
        if repository['full_name'].lower() != self.full_name:
            return False
        if event == 'push':
            return payload.get('ref') in (self._branch(self.source_ref),
                                          self._branch(self.communications_ref))
        if event == 'workflow_run':
            run = payload.get('workflow_run')
            return (isinstance(run, dict) and
                    run.get('head_branch') == self.source_ref.removeprefix('refs/heads/'))
        return False


class WebhookFault(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class GitHubWebhookHints:
    """WSGI receiver; never executes provider calls inside a webhook request."""
    MAX_BYTES = 1024 * 1024
    PATH = '/webhooks/github'
    _signature = re.compile(r'^sha256=[a-fA-F0-9]{64}$')

    def __init__(self, writer: WorkforceStorage,
                 bindings: tuple[GitHubWebhookBinding, ...],
                 on_hint: Callable[[], None]):
        if not isinstance(writer, WorkforceStorage) or not callable(on_hint):
            raise TypeError('webhook requires the real C writer and nonblocking signal')
        if not isinstance(bindings, tuple) or not bindings or any(
                not isinstance(binding, GitHubWebhookBinding) for binding in bindings):
            raise TypeError('trusted GitHub webhook bindings required')
        self.writer = writer
        self.bindings = bindings
        self.on_hint = on_hint

    @staticmethod
    def _unique_pairs(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('duplicate JSON property')
            result[k] = v
        return result

    def _process(self, environ: Mapping) -> int:
        if environ.get('REQUEST_METHOD') != 'POST':
            raise WebhookFault(405, 'method not allowed')
        length = environ.get('CONTENT_LENGTH', '')
        if not isinstance(length, str) or not length.isascii() or not length.isdigit():
            raise WebhookFault(400, 'invalid content length')
        count = int(length)
        if not 1 <= count <= self.MAX_BYTES:
            raise WebhookFault(413 if count > self.MAX_BYTES else 400, 'invalid body size')
        content_type = environ.get('CONTENT_TYPE', '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            raise WebhookFault(415, 'JSON content type required')
        signature = environ.get('HTTP_X_HUB_SIGNATURE_256', '')
        if not isinstance(signature, str) or not self._signature.fullmatch(signature):
            raise WebhookFault(401, 'missing or invalid webhook signature')
        delivery = environ.get('HTTP_X_GITHUB_DELIVERY', '')
        try:
            delivery = str(uuid.UUID(delivery))
        except (TypeError, AttributeError, ValueError) as exc:
            raise WebhookFault(400, 'invalid webhook delivery identity') from exc
        event = environ.get('HTTP_X_GITHUB_EVENT', '')
        if event not in ('push', 'workflow_run'):
            return 204  # Unrecognized events do not grant authority or trigger work.
        stream = environ.get('wsgi.input')
        if not hasattr(stream, 'read'):
            raise WebhookFault(400, 'missing body stream')
        body = stream.read(count)
        if not isinstance(body, bytes) or len(body) != count:
            raise WebhookFault(400, 'truncated webhook body')
        try:
            payload = json.loads(body.decode('utf-8'),object_pairs_hook=self._unique_pairs,
                                 parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
        except (ValueError, UnicodeError) as exc:
            raise WebhookFault(400, 'invalid webhook JSON') from exc
        if not isinstance(payload, dict):
            raise WebhookFault(400, 'invalid webhook JSON object')
        eligible=[]
        for binding in self.bindings:
            if not binding.accepts(event, payload):
                continue
            expected='sha256='+hmac.new(binding.secret,body,hashlib.sha256).hexdigest()
            if hmac.compare_digest(signature.lower(), expected):
                eligible.append(binding)
        if not eligible:
            raise WebhookFault(401, 'unverified repository/ref webhook')
        fingerprint=hashlib.sha256(event.encode('utf-8')+b'\x00'+body).hexdigest()
        for binding in eligible:
            project = binding.project_id
            def commit(_tx):
                # The delivery ID and original payload digest are stored
                # atomically in C's existing idempotency/result journal.
                return {'project_id': project, 'delivery_id': delivery,
                        'event': event, 'state': 'recorded'}
            try:
                self.writer.command(project,'G.webhookHint',delivery,fingerprint,commit)
            except WorkforceFault as exc:
                raise WebhookFault(409 if exc.code == 'conflict' else 503,
                                   'webhook delivery could not be recorded') from exc
            except Exception as exc:
                raise WebhookFault(503, 'webhook delivery could not be recorded') from exc
        # Signal failure does not undo a committed C delivery receipt: the
        # independent periodic readback loops still recover missed events.
        try:
            self.on_hint()
        except Exception:
            pass
        return 202

    def __call__(self, environ: dict, start_response):
        if environ.get('PATH_INFO') != self.PATH:
            status=404
        else:
            try:
                status=self._process(environ)
            except WebhookFault as exc:
                status=exc.status
        labels={202:'Accepted',204:'No Content',400:'Bad Request',401:'Unauthorized',
                404:'Not Found',405:'Method Not Allowed',409:'Conflict',
                413:'Content Too Large',415:'Unsupported Media Type',
                503:'Service Unavailable'}
        body = b''
        start_response(f'{status} {labels.get(status,"Error")}',[
            ('Content-Type','text/plain; charset=utf-8'),
            ('Content-Length',str(len(body))),('Cache-Control','no-store')])
        return [body]


class GitHubWebhookHintsBuilder:
    def __init__(self, bindings: tuple[GitHubWebhookBinding, ...]):
        self.bindings = bindings

    def for_writer(self, writer: WorkforceStorage, on_hint: Callable[[], None]):
        return GitHubWebhookHints(writer, self.bindings, on_hint)


class WebhookMuxWSGI:
    """Add a separate signed ingress without changing the 72 operator routes."""
    def __init__(self, operator_wsgi, github_webhook: GitHubWebhookHints):
        if not callable(operator_wsgi) or not isinstance(github_webhook, GitHubWebhookHints):
            raise TypeError('real operator and authenticated webhook applications required')
        self.operator_wsgi = operator_wsgi
        self.github_webhook = github_webhook

    def __call__(self, environ, start_response):
        if environ.get('PATH_INFO') == GitHubWebhookHints.PATH:
            return self.github_webhook(environ, start_response)
        return self.operator_wsgi(environ, start_response)
