"""Continuous-response GitHub worker inbox and checkpoint protocol.

A shift is one human-started ChatGPT response. A verified Git commit is the
transport cursor, while individual command IDs identify distinct assignments.
Neither dispatch nor receipt publication starts or wakes a ChatGPT response.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol, Mapping, Any, Callable
import json
import re


class ShiftProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ShiftBatch:
    """A completely verified scan of a single GitHub branch head."""
    messages: tuple[Mapping[str, Any], ...]
    head_sha: str

    def __post_init__(self) -> None:
        if re.fullmatch(r'[a-f0-9]{40}', self.head_sha) is None:
            raise ShiftProtocolError('transport cursor must be a verified Git commit')


class ShiftTransport(Protocol):
    def read_messages(self, *, project_id: str, worker_id: str,
                      after: str | None) -> ShiftBatch: ...
    def publish_receipt(self, *, project_id: str, worker_id: str, shift_id: str,
                        command_id: str, command_digest: str, status: str,
                        result_digest: str) -> str: ...
    def checkpoint(self, *, project_id: str, worker_id: str, shift_id: str,
                   cursor: str | None, processed: tuple[str, ...],
                   fingerprints: Mapping[str, str]) -> None: ...


@dataclass(frozen=True)
class ShiftCommand:
    project_id: str
    worker_id: str
    shift_id: str
    assignment_id: str
    command_id: str
    kind: str
    payload_digest: str
    source_revision: str

    @classmethod
    def parse(cls, doc: Mapping[str, Any]) -> 'ShiftCommand':
        needed = {'project_id', 'worker_id', 'shift_id', 'assignment_id', 'command_id',
                  'kind', 'payload_digest', 'source_revision'}
        if not isinstance(doc, Mapping) or set(doc) != needed:
            raise ShiftProtocolError('command envelope keys differ')
        if any(not isinstance(doc[k], str) or not doc[k] for k in needed):
            raise ShiftProtocolError('empty command field')
        if re.fullmatch(r'[a-f0-9]{64}', doc['payload_digest']) is None:
            raise ShiftProtocolError('invalid payload hash')
        if doc['kind'] not in {'ASSIGN', 'NOTE', 'QUESTION', 'ANSWER', 'REVIEW', 'STOP'}:
            raise ShiftProtocolError('unsupported command kind')
        if doc['kind'] == 'STOP':
            # STOP has no task assignment in persistent storage. The wire
            # envelope binds its assignment slot to the shift identity instead.
            if doc['assignment_id'] != doc['shift_id'] or doc['source_revision'] != 'shift_stop':
                raise ShiftProtocolError('STOP envelope must be bound to its shift')
        return cls(**dict(doc))


@dataclass
class ShiftInbox:
    project_id: str
    worker_id: str
    shift_id: str
    source_revision: str | None
    transport: ShiftTransport
    verify_assignment: Callable[[ShiftCommand], bool] | None = None
    cursor: str | None = None
    processed: set[str] = field(default_factory=set)
    fingerprints: dict[str, str] = field(default_factory=dict)
    stopping: bool = False
    _pending: dict[str, ShiftCommand] = field(default_factory=dict, init=False, repr=False)
    _batch_head: str | None = field(default=None, init=False, repr=False)

    def poll(self) -> list[ShiftCommand]:
        if self.stopping:
            return []
        # Do not consume another GitHub cursor while commands from the current
        # verified snapshot have not been acknowledged through real receipts.
        if self._pending:
            return list(self._pending.values())
        batch = self.transport.read_messages(project_id=self.project_id,
                                             worker_id=self.worker_id, after=self.cursor)
        if not isinstance(batch, ShiftBatch):
            raise ShiftProtocolError('transport must return a complete verified GitHub batch')
        if self.cursor == batch.head_sha and batch.messages:
            raise ShiftProtocolError('same Git ref cannot contain new immutable commands')
        unseen: list[ShiftCommand] = []
        seen_in_batch: dict[str, str] = {}
        for record in batch.messages:
            cmd = ShiftCommand.parse(record)
            if (cmd.project_id, cmd.worker_id, cmd.shift_id) != (self.project_id, self.worker_id, self.shift_id):
                raise ShiftProtocolError('message is not addressed to active worker shift')
            # Distinct assignments within one shift may legitimately use distinct
            # source baselines. The production adapter must validate each against
            # the actual authorized assignment; a fixed revision is only suitable
            # for a deliberately pinned test or single-baseline workflow.
            if self.verify_assignment is not None:
                if not self.verify_assignment(cmd):
                    raise ShiftProtocolError('command source revision or assignment not authorized')
            elif cmd.kind != 'STOP' and (not self.source_revision or cmd.source_revision != self.source_revision):
                raise ShiftProtocolError('stale or foreign source revision')
            # STOP must be shift-bound, not compared to a task's pinned source
            # revision. The GitHub transport verifies immutable branch bytes and
            # the addressed recipient; a supplied authorization callback must
            # independently approve STOP in the current shift as well.
            fingerprint = sha256(json.dumps(dict(record), sort_keys=True,
                              separators=(',', ':'), ensure_ascii=True).encode('utf-8')).hexdigest()
            if cmd.command_id in seen_in_batch and seen_in_batch[cmd.command_id] != fingerprint:
                raise ShiftProtocolError('duplicate command ID has divergent immutable envelope')
            if cmd.command_id in self.fingerprints and self.fingerprints[cmd.command_id] != fingerprint:
                raise ShiftProtocolError('previously received command bytes have changed')
            if cmd.command_id in seen_in_batch:
                continue
            seen_in_batch[cmd.command_id] = fingerprint
            if cmd.command_id not in self.processed:
                unseen.append(cmd)
        self.fingerprints.update(seen_in_batch)
        self._pending = {cmd.command_id: cmd for cmd in unseen}
        self._batch_head = batch.head_sha
        if not self._pending:
            self._commit_checkpoint(batch.head_sha)
        return unseen

    def _commit_checkpoint(self, cursor: str) -> None:
        self.transport.checkpoint(project_id=self.project_id, worker_id=self.worker_id,
                                  shift_id=self.shift_id, cursor=cursor,
                                  processed=tuple(sorted(self.processed)),
                                  fingerprints=dict(sorted(self.fingerprints.items())))
        self.cursor = cursor

    def record_result(self, command: ShiftCommand, *, result: Mapping[str, Any], status: str) -> str:
        if command.command_id in self.processed:
            raise ShiftProtocolError('command already processed; reconcile original receipt')
        if command.command_id not in self._pending or self._pending[command.command_id] != command:
            raise ShiftProtocolError('command is not in the current verified GitHub batch')
        if status not in ('accepted', 'completed', 'blocked', 'failed', 'stopped'):
            raise ShiftProtocolError('invalid receipt status')
        payload = json.dumps(dict(result), sort_keys=True, separators=(',', ':'),
                             allow_nan=False, ensure_ascii=True).encode()
        result_digest = sha256(payload).hexdigest()
        evidence = self.transport.publish_receipt(project_id=self.project_id, worker_id=self.worker_id,
                                                  shift_id=self.shift_id, command_id=command.command_id,
                                                  command_digest=self.fingerprints[command.command_id],
                                                  status=status, result_digest=result_digest)
        if not isinstance(evidence, str) or re.fullmatch(r'[a-f0-9]{40}', evidence) is None:
            raise ShiftProtocolError('receipt lacks a verified GitHub commit SHA')
        self.processed.add(command.command_id)
        del self._pending[command.command_id]
        if command.kind == 'STOP' and status == 'stopped':
            self.stopping = True
        if not self._pending and self._batch_head is not None:
            self._commit_checkpoint(self._batch_head)
            self._batch_head = None
        else:
            # Persist processed IDs immediately; do not advance past pending work.
            self._commit_checkpoint(self.cursor) if self.cursor is not None else self.transport.checkpoint(
                project_id=self.project_id, worker_id=self.worker_id, shift_id=self.shift_id,
                cursor=None, processed=tuple(sorted(self.processed)),
                fingerprints=dict(sorted(self.fingerprints.items())))
        return evidence
