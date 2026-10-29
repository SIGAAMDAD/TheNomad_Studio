"""Human-facing R9 command client. Authentication is exclusively CAB-H/P2.

No former Component or fleet launch API is retained. The caller must provide
an actual CAB-H authenticated Result and a frozen CommandPort implementation.
"""
from __future__ import annotations
from typing import Any
from collections.abc import Mapping
from nomad_studio.production.director.wire import valid_id
from nomad_studio.production.director.wire import Result, Record


class OwnerConsole:
    def __init__(self, command_port: Any):
        self.port = command_port

    def submit(self, command: Record, authenticated_owner: Result) -> Result:
        return self.port.submit_owner_command(command, authenticated_owner)

    def feature(self, authenticated_owner: Result, feature_id: str) -> Result:
        if not isinstance(authenticated_owner,Result) or authenticated_owner.status != 'OK' or authenticated_owner.code != 'OK' or not valid_id(authenticated_owner.receipt_ref) or not isinstance(authenticated_owner.record,Mapping) or not valid_id(authenticated_owner.record.get('owner_principal_id')) or authenticated_owner.record.get('simulated') is True:
            return Result('REJECTED','UNAUTHENTICATED')
        return self.port.get_feature_status(authenticated_owner.record['owner_principal_id'], feature_id)

    def studio(self, authenticated_owner: Result, project_id: str | None = None) -> Result:
        if not isinstance(authenticated_owner,Result) or authenticated_owner.status != 'OK' or authenticated_owner.code != 'OK' or not valid_id(authenticated_owner.receipt_ref) or not isinstance(authenticated_owner.record,Mapping) or not valid_id(authenticated_owner.record.get('owner_principal_id')) or authenticated_owner.record.get('simulated') is True:
            return Result('REJECTED','UNAUTHENTICATED')
        return self.port.get_studio_status(authenticated_owner.record['owner_principal_id'], project_id)
