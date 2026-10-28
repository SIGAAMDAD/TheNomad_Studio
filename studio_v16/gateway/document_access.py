"""Pinned document reader and fenced writer lease gateway."""
import hashlib

from studio_v16.db.store import GatewayContext, Denied
from .gateway import AdmissionError


class DocumentAccess:
    def __init__(self, *, coordination, drive, audit):
        self._coordination, self._drive, self._audit = coordination, drive, audit

    @staticmethod
    def _context(principal):
        if principal is None or principal.auth_method != 'MTLS':
            raise AdmissionError('IDENTITY_UNVERIFIED')
        return GatewayContext(principal.principal_id, verified_gateway=True)

    def read(self, file_id, revision, exported_sha256, principal):
        context = self._context(principal)
        try:
            metadata = self._coordination.document(file_id, revision, exported_sha256, context)
        except Denied as exc:
            raise AdmissionError(exc.reason) from None
        # The provider verifies the actual pinned bytes, not a client's hash claim.
        content = self._drive.read_pinned(file_id, revision, metadata['exported_sha256'])
        if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != exported_sha256:
            raise AdmissionError('DOCUMENT_CONFLICT')
        self._audit.emit(dict(event='DOCUMENT_READ', principal_id=principal.principal_id,
                              file_id=file_id, revision=revision, exported_sha256=exported_sha256))
        return content

    def claim_writer(self, file_id, expected_revision, expected_generation, leader_epoch, principal):
        try:
            result = self._coordination.claim_document(file_id, expected_revision, expected_generation, leader_epoch,
                                                       self._context(principal))
        except Denied as exc:
            raise AdmissionError(exc.reason) from None
        self._audit.emit(dict(event='DOCUMENT_WRITER_CLAIM', principal_id=principal.principal_id,
                              file_id=file_id, revision=expected_revision, writer_generation=result['writer_generation']))
        return result
