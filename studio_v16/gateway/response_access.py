"""Authenticated response port; selection is a trusted server callback."""
from dataclasses import replace

from .document_access import DocumentAccess
from .gateway import AdmissionError


class ResponseAccess:
    def __init__(self, coordination, principal, *, eligible_command, source_verifier=None):
        self._coordination = coordination
        self._principal = principal
        self._eligible_command = eligible_command
        self._source_verifier = source_verifier

    def _context(self):
        return DocumentAccess._context(self._principal)

    def start(self, response_id, worker_id, department):
        return self._coordination.start_response(response_id, worker_id, department, self._context())

    def observe_control(self, response_id):
        return self._coordination.observe_control(response_id, self._context())

    def next_claim(self, response_id):
        command = self._eligible_command(response_id, self._principal)
        if command is None:
            return None
        if self._source_verifier is None:
            raise AdmissionError('REBASE_REQUIRED')
        context = self._context()
        proof = self._source_verifier.verify(command, context.principal_id)
        context = replace(context, verified_source_base=True, source_proof=proof)
        result = self._coordination.claim_for_response(response_id, command, context)
        if result['result'] not in ('COMMITTED', 'DUPLICATE'):
            raise AdmissionError(result['denial_code'])
        return dict(command, claim_receipt=result)

    def record_job(self, response_id, attempt_id, acceptance_ids, evidence):
        return self._coordination.record_response_job(response_id, attempt_id, acceptance_ids, evidence, self._context())

    def finish(self, response_id, *, stop, resume_capsule, failures):
        return self._coordination.finish_response(response_id, self._context(), stop=stop,
                                                   resume_capsule=resume_capsule, failures=failures)
