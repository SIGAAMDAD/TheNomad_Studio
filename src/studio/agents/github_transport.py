"""Verified GitHub transport for manually running browser worker shifts.

Reads one fully verified Git ref snapshot per checkpoint. Receipt writes are
immutable and require an externally persisted effect intent. No model runtime
or background polling is launched.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Callable, Mapping

from studio.integrations.github import GitHub
from studio.integrations.common import IntegrationError
from .shift_runtime import ShiftBatch, ShiftCommand, ShiftProtocolError


def canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


class GitHubShiftTransport:
    def __init__(self, github: GitHub, *, shift_id: str,
                 record_intent: Callable[[str, str], bool],
                 save_checkpoint: Callable[..., None], max_pages: int = 50):
        if not shift_id or max_pages < 1 or max_pages > 500:
            raise ValueError('shift ID and bounded scan count required')
        self.github = github
        self.shift_id = shift_id
        self.record_intent = record_intent
        self.save_checkpoint = save_checkpoint
        self.max_pages = max_pages

    def read_messages(self, *, project_id: str, worker_id: str,
                      after: str | None) -> ShiftBatch:
        if project_id != self.github.channel.project_id:
            raise ShiftProtocolError('wrong project for Github shift transport')
        prefix = self.github.channel.path('mail', worker_id, 'probe').rsplit('/', 1)[0] + '/'
        offset = 0
        expected_head = None
        messages: list[Mapping[str, Any]] = []
        for _ in range(self.max_pages):
            page = self.github.scan_since(after, limit=100, offset=offset,
                                          expected_head=expected_head)
            if expected_head is None:
                expected_head = page.head_sha
            for entry in page.entries:
                if not entry.path.startswith(prefix):
                    continue
                try:
                    doc = json.loads(entry.body.decode('utf-8'))
                except (ValueError, UnicodeError) as exc:
                    raise ShiftProtocolError('invalid verified Github command bytes') from exc
                if not isinstance(doc, dict) or 'shift_id' not in doc:
                    continue  # Other existing relay protocols belong to their own reader.
                cmd = ShiftCommand.parse(doc)
                if cmd.project_id != project_id or cmd.worker_id != worker_id:
                    raise ShiftProtocolError('command is addressed to another worker or project')
                # The same persistent conversation's mailbox retains commands
                # from earlier shifts. Those commands are immutable history.
                if cmd.shift_id != self.shift_id:
                    continue
                if entry.path != self.github.channel.path('mail', worker_id, cmd.command_id):
                    raise ShiftProtocolError('verified command path and ID differ')
                if entry.body != canonical(doc):
                    raise ShiftProtocolError('command must use exact canonical GitHub bytes')
                messages.append(doc)
            if page.complete_cursor is not None:
                return ShiftBatch(tuple(messages), page.complete_cursor)
            if page.next_offset is None or page.next_offset <= offset:
                raise ShiftProtocolError('GitHub scan page did not advance')
            offset = page.next_offset
        raise ShiftProtocolError('GitHub scan exceeded page limit; cursor unchanged')

    def publish_receipt(self, *, project_id: str, worker_id: str, shift_id: str,
                        command_id: str, command_digest: str, status: str,
                        result_digest: str) -> str:
        if project_id != self.github.channel.project_id or shift_id != self.shift_id:
            raise ShiftProtocolError('receipt project/shift mismatch')
        content = dict(protocol_version='2', project_id=project_id, worker_id=worker_id,
                       shift_id=shift_id, command_id=command_id, command_digest=command_digest,
                       status=status, result_digest=result_digest)
        # Stable receipt bytes and file ID allow exact same-effect recovery on ambiguous writes.
        receipt_id = 'rcpt_' + sha256(canonical(content)).hexdigest()
        content['receipt_id'] = receipt_id
        body = canonical(content)
        path = self.github.channel.path('receipts', worker_id, receipt_id, message_id=command_id)
        body_digest = sha256(body).hexdigest()
        if not self.record_intent(path, body_digest):
            raise ShiftProtocolError('durable receipt effect intent was not recorded')
        observed = self.github.publish(path, body, expected_digest=body_digest,
                                       effect_intent_recorded=True)
        if observed.path != path or observed.body_sha256 != body_digest or observed.body != body:
            raise ShiftProtocolError('remote receipt did not match published evidence')
        return observed.commit_sha

    def checkpoint(self, *, project_id: str, worker_id: str, shift_id: str,
                   cursor: str | None, processed: tuple[str, ...],
                   fingerprints: Mapping[str, str]) -> None:
        if project_id != self.github.channel.project_id or shift_id != self.shift_id:
            raise ShiftProtocolError('checkpoint project/shift mismatch')
        self.save_checkpoint(project_id=project_id, worker_id=worker_id, shift_id=shift_id,
                             cursor=cursor, processed=processed, fingerprints=fingerprints)
