"""Frozen WG-F operator adapter for actual budget, reporting and notification services."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from studio.api.errors import ApiError
from studio.operations.core import DomainError, Operations, Scope
from studio.operations.reporting import ReportRelay, ProfileStore, request_report
from studio.operations.core import VerifiedBlobWriter


class OperationsCommands:
    """Only adapter signatures live here; F's C-backed runner owns all mutations."""

    atomic_operations = frozenset({'configureBudget', 'requestReport', 'ackNotification'})
    operation_ids = (
        'configureBudget', 'getBudgets', 'listReports',
        'requestReport', 'listNotifications', 'ackNotification',
    )

    def __init__(self, operations: Operations, *, profiles: ProfileStore,
                 blobs: VerifiedBlobWriter, relay: ReportRelay | None = None):
        needed = ('configure_budget','get_budgets','list_reports',
                  'list_notifications','ack_notification')
        absent = [name for name in needed if not callable(getattr(operations,name,None))]
        if absent or not callable(getattr(profiles,'load_verified',None)) or not callable(getattr(blobs,'store_verified',None)):
            raise ValueError('WG-F operations, profile source and verified blobs must be connected')
        self.operations, self.profiles, self.blobs, self.relay = operations, profiles, blobs, relay

    @staticmethod
    def _scope(actor: Mapping[str, Any], project_id: str) -> Scope:
        if not isinstance(actor, Mapping):
            raise ApiError('forbidden','authenticated operator identity required')
        principal = actor.get('principal_id')
        proof = actor.get('trusted_proof_ref')
        issuer = actor.get('issuer')
        projects = actor.get('allowed_project_ids')
        if (not isinstance(principal,str) or not principal
                or not isinstance(proof,str) or not proof
                or not isinstance(issuer,str) or not issuer
                or not isinstance(projects,(list,tuple,set,frozenset))
                or any(not isinstance(p,str) or not p for p in projects)):
            raise ApiError('forbidden','invalid verified F actor')
        scope = Scope(project_id,principal,proof,frozenset(projects))
        try:
            scope.check()
        except DomainError as error:
            raise ApiError(error.code,str(error)) from error
        return scope

    @staticmethod
    def _run(work):
        try:
            return work()
        except DomainError as error:
            raise ApiError(error.code,str(error)) from error

    def configureBudget(self, actor, project_id, budget_id, request):
        scope = self._scope(actor, project_id)
        return self._run(lambda: self.operations.configure_budget(scope, budget_id=budget_id, **request))

    def getBudgets(self, actor, project_id):
        scope = self._scope(actor, project_id)
        return self._run(lambda: self.operations.get_budgets(scope))

    def listReports(self, actor, project_id):
        scope = self._scope(actor, project_id)
        return self._run(lambda: self.operations.list_reports(scope))

    def requestReport(self, actor, project_id, request):
        scope = self._scope(actor, project_id)
        return self._run(lambda: request_report(
            self.operations,scope,profiles=self.profiles,blobs=self.blobs,
            relay=self.relay,**request))

    def listNotifications(self, actor, project_id):
        scope = self._scope(actor, project_id)
        return self._run(lambda: self.operations.list_notifications(scope))

    def ackNotification(self, actor, project_id, notification_id, request):
        scope = self._scope(actor, project_id)
        return self._run(lambda: self.operations.ack_notification(
            scope,notification_id=notification_id,**request))
