"""Frozen operator adapter for WG-E independent QA and oversight."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from studio.api.errors import ApiError
from studio.assurance.service import AssuranceError, AssuranceService


class AssuranceCommands:
    """WG-E storage owns transactions; J owns bearer and project-grant checks."""

    atomic_operations = frozenset({
        'requestReview', 'submitReview', 'openOversightCase', 'resolveOversightCase',
    })
    operation_ids = (
        'requestReview', 'submitReview', 'listReviews',
        'openOversightCase', 'resolveOversightCase', 'listOversightCases',
    )

    def __init__(self, service: AssuranceService):
        missing = [op for op in self.operation_ids
                   if not callable(getattr(service, op, None))]
        if missing:
            raise ValueError('missing assurance operations: '+', '.join(missing))
        self.service = service

    def _call(self, name: str, actor: Mapping[str, Any], project_id: str,
              *, request: dict[str, Any] | None = None, **path: str) -> dict:
        if (not isinstance(actor, Mapping)
                or not all(isinstance(actor.get(field), str) and actor[field]
                           for field in ('principal_id','issuer','trusted_proof_ref'))
                or not isinstance(actor.get('allowed_project_ids'), (list,tuple,set,frozenset))
                or project_id not in actor['allowed_project_ids']):
            raise ApiError('forbidden','verified assurance project actor required')
        try:
            if name in self.atomic_operations:
                if not isinstance(request, dict):
                    raise ApiError('invalid', 'request body required')
                return getattr(self.service,name)(actor,project_id,**path,request=request)
            if request is not None:
                raise ApiError('invalid','read-only operation cannot mutate')
            return getattr(self.service,name)(actor,project_id,**path)
        except AssuranceError as error:
            raise ApiError(error.code,str(error)) from error

    def requestReview(self, actor, project_id, request):
        return self._call('requestReview', actor, project_id, request=request)

    def submitReview(self, actor, project_id, review_id, request):
        return self._call('submitReview', actor, project_id, review_id=review_id, request=request)

    def listReviews(self, actor, project_id):
        return self._call('listReviews', actor, project_id)

    def openOversightCase(self, actor, project_id, request):
        return self._call('openOversightCase', actor, project_id, request=request)

    def resolveOversightCase(self, actor, project_id, case_id, request):
        return self._call('resolveOversightCase', actor, project_id, case_id=case_id, request=request)

    def listOversightCases(self, actor, project_id):
        return self._call('listOversightCases', actor, project_id)
