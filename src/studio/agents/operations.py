"""Operator API for persistent browser workers and multi-assignment work shifts.

No method here creates or resumes a ChatGPT response. Evidence verification is
supplied by the trusted GitHub readback service, never by the request body.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping
from studio.api.errors import ApiError

from studio.workforce import Workforce, WorkforceFault


EvidenceVerifier = Callable[[str, str, Mapping[str, Any]], bool]


class WorkerOperations:
    # Domain commands own durable transactions and idempotent results; the HTTP
    # coordinator must not open a second independent transaction around them.
    atomic_operations = frozenset({
        'registerWorker', 'beginShift', 'stopShift', 'endShift', 'observeCommandReceipt'
    })

    @staticmethod
    def _run(command: Callable[[], dict]) -> dict:
        try:
            return command()
        except WorkforceFault as error:
            mapped = {'invalid': 'invalid', 'conflict': 'conflict',
                      'forbidden': 'forbidden', 'unknown': 'unknown_effect'}
            raise ApiError(mapped.get(error.code, 'internal'), str(error)) from error

    def __init__(self, workforce: Workforce, *, verify_receipt: EvidenceVerifier):
        self._workforce = workforce
        self._verify_receipt = verify_receipt

    def registerWorker(self, actor: Mapping[str, Any], project_id: str, request: dict) -> dict:
        return self._run(lambda: self._workforce.register(actor, project_id, request))

    def beginShift(self, actor: Mapping[str, Any], project_id: str,
                   worker_id: str, request: dict) -> dict:
        if not isinstance(worker_id, str) or not worker_id:
            raise ApiError('invalid', 'worker_id required')
        if set(request) != {'request_id', 'shift_id'}:
            raise ApiError('invalid', 'unexpected begin-shift fields')
        return self._run(lambda: self._workforce.begin_shift(actor, project_id, {**request, 'worker_id': worker_id}))

    def stopShift(self, actor: Mapping[str, Any], project_id: str,
                  worker_id: str, shift_id: str, request: dict) -> dict:
        if set(request) != {'request_id', 'reason'}:
            raise ApiError('invalid', 'unexpected stop-shift fields')
        return self._run(lambda: self._workforce.request_stop(actor, project_id,
                    {**request, 'worker_id': worker_id, 'shift_id': shift_id}))

    def listWorkers(self, actor: Mapping[str, Any], project_id: str) -> dict:
        self._workforce.require_action(actor, project_id, 'listWorkers')
        def read(tx):
            rows = tx.all('''SELECT worker_id,role_id,state FROM worker_conversations
                             WHERE project_id=? ORDER BY worker_id''', (project_id,))
            return {'items': [dict(r) for r in rows]}
        return self._run(lambda: self._workforce.storage.read(project_id, read))

    def observeHello(self, actor: Mapping[str, Any], project_id: str, request: dict) -> dict:
        """Coordinator-only verified GitHub ingestion; never directly user-supplied proof."""
        return self._run(lambda: self._workforce.observe_hello(actor, project_id, request,
                    verify=lambda record: self._verify_receipt('hello', project_id, record)))

    def endShift(self, actor: Mapping[str, Any], project_id: str,
                 shift_id: str, request: dict) -> dict:
        if set(request) != {'request_id', 'receipt_commit_sha', 'receipt_digest'}:
            raise ApiError('invalid', 'unexpected final receipt fields')
        return self._run(lambda: self._workforce.end_shift(actor, project_id, {**request, 'shift_id': shift_id},
                    verify=lambda record: self._verify_receipt('final', project_id, record)))

    def observeCommandReceipt(self, actor: Mapping[str, Any], project_id: str,
                              command_id: str, request: dict) -> dict:
        required = {'request_id', 'receipt_id', 'worker_id', 'shift_id',
                    'kind', 'result_digest', 'receipt_commit_sha'}
        if set(request) != required:
            raise ApiError('invalid', 'unexpected command receipt fields')
        return self._run(lambda: self._workforce.observe_command_receipt(actor, project_id,
                    {**request, 'command_id': command_id},
                    verify=lambda record: self._verify_receipt('command', project_id, record)))
