"""Owner-scoped leader/control operations; authentication is transport-owned."""
from studio_v16.db.authority_contract import Denied
from .document_access import DocumentAccess
from .gateway import AdmissionError


class LeadershipAccess:
    def __init__(self, coordination):
        self._coordination = coordination

    def handoff(self, department, new_principal_id, expected_epoch, decision_id, principal):
        try:
            return self._coordination.handoff(department, new_principal_id, expected_epoch, decision_id,
                                               DocumentAccess._context(principal))
        except Denied as exc:
            raise AdmissionError(exc.reason) from None

    def control(self, response_id, action, expected_epoch, nonce, leader_epoch, principal, *, owner_decision_id=None):
        try:
            return self._coordination.control(response_id, action, expected_epoch, nonce, leader_epoch,
                                               DocumentAccess._context(principal), owner_decision_id=owner_decision_id)
        except Denied as exc:
            raise AdmissionError(exc.reason) from None
