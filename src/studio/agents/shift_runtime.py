"""Continuous-response worker protocol. No model/API launcher and no task-bound session.

One human-started ChatGPT response owns a ShiftInbox instance. Each poll reads
verified addressed GitHub envelopes. The worker can process multiple successive
assignments before publishing a single end-of-shift receipt. Persist messages,
checkpoints and receipts through the passed repository-backed adapter.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol, Mapping, Any
import json

class ShiftProtocolError(ValueError):
    pass

class ShiftTransport(Protocol):
    def read_messages(self, *, project_id: str, worker_id: str, after: str | None) -> list[Mapping[str, Any]]: ...
    def publish_receipt(self, *, project_id: str, worker_id: str, shift_id: str,
                        command_id: str, status: str, result_digest: str) -> str: ...
    def checkpoint(self, *, project_id: str, worker_id: str, shift_id: str,
                   cursor: str | None, processed: tuple[str, ...]) -> None: ...

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
        needed = {"project_id","worker_id","shift_id","assignment_id","command_id","kind","payload_digest","source_revision"}
        if not isinstance(doc, Mapping) or set(doc) != needed:
            raise ShiftProtocolError("command envelope keys differ")
        if any(not isinstance(doc[k], str) or not doc[k] for k in needed):
            raise ShiftProtocolError("empty command field")
        if len(doc['payload_digest']) != 64 or any(c not in '0123456789abcdef' for c in doc['payload_digest']):
            raise ShiftProtocolError("invalid payload hash")
        if doc['kind'] not in {'ASSIGN','NOTE','QUESTION','ANSWER','REVIEW','STOP'}:
            raise ShiftProtocolError("unsupported command kind")
        return cls(**dict(doc))

@dataclass
class ShiftInbox:
    project_id: str
    worker_id: str
    shift_id: str
    source_revision: str
    transport: ShiftTransport
    cursor: str | None = None
    processed: set[str] = field(default_factory=set)
    fingerprints: dict[str, str] = field(default_factory=dict)
    stopping: bool = False

    def poll(self) -> list[ShiftCommand]:
        if self.stopping:
            return []
        batch = self.transport.read_messages(project_id=self.project_id,
                                            worker_id=self.worker_id, after=self.cursor)
        unseen: list[ShiftCommand] = []
        seen_in_batch: dict[str, str] = {}
        for record in batch:
            cmd = ShiftCommand.parse(record)
            if (cmd.project_id,cmd.worker_id,cmd.shift_id) != (self.project_id,self.worker_id,self.shift_id):
                raise ShiftProtocolError("message is not addressed to active worker shift")
            if cmd.source_revision != self.source_revision:
                raise ShiftProtocolError("stale or foreign source revision")
            fingerprint = sha256(json.dumps(dict(record), sort_keys=True,
                              separators=(',', ':')).encode('utf-8')).hexdigest()
            if cmd.command_id in seen_in_batch and seen_in_batch[cmd.command_id] != fingerprint:
                raise ShiftProtocolError('duplicate command ID has divergent immutable envelope')
            if cmd.command_id in self.fingerprints and self.fingerprints[cmd.command_id] != fingerprint:
                raise ShiftProtocolError('previously received command bytes have changed')
            if cmd.command_id in seen_in_batch:
                continue
            seen_in_batch[cmd.command_id] = fingerprint
            self.fingerprints[cmd.command_id] = fingerprint
            if cmd.command_id not in self.processed:
                unseen.append(cmd)
        return unseen

    def record_result(self, command: ShiftCommand, *, result: Mapping[str, Any], status: str) -> str:
        if command.command_id in self.processed:
            raise ShiftProtocolError("command already processed; reconcile original receipt")
        if (command.project_id,command.worker_id,command.shift_id) != (self.project_id,self.worker_id,self.shift_id):
            raise ShiftProtocolError("foreign receipt")
        if status not in ('accepted','completed','blocked','failed','stopped'):
            raise ShiftProtocolError("invalid receipt status")
        payload=json.dumps(dict(result),sort_keys=True,separators=(',',':')).encode()
        result_digest=sha256(payload).hexdigest()
        evidence=self.transport.publish_receipt(project_id=self.project_id,worker_id=self.worker_id,
                                                shift_id=self.shift_id,command_id=command.command_id,
                                                status=status,result_digest=result_digest)
        if not isinstance(evidence,str) or not evidence:
            raise ShiftProtocolError("receipt lacks remote publication evidence")
        self.processed.add(command.command_id)
        self.cursor=command.command_id
        self.transport.checkpoint(project_id=self.project_id, worker_id=self.worker_id,
                                  shift_id=self.shift_id, cursor=self.cursor,
                                  processed=tuple(sorted(self.processed)),
                                  fingerprints=dict(sorted(self.fingerprints.items())))
        if command.kind == 'STOP' and status == 'stopped':
            self.stopping=True
        return evidence
