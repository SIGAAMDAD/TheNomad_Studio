"""Durable C-backed G provider account attachment repository.

Provider identity must have been read back by ConnectorService before calling
this port. Persist credential references only; never store bearer tokens here.
"""
from __future__ import annotations

from hashlib import sha256

from studio.integrations.common import IntegrationError, identifier
from studio.workforce import WorkforceFault
from .workforce import WorkforceStorage


class ConnectorAPSWRepository:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("real C APSW writer required")
        self._writer = writer

    def record_connector(self, project_id: str, provider_key: str,
                         credential_ref: str, account_id: str, request_id: str) -> None:
        for field in (project_id, provider_key, credential_ref, account_id, request_id):
            identifier(field)
        if provider_key not in ("github", "google_drive"):
            raise IntegrationError("capability_unavailable", "connector provider unavailable")
        payload = sha256(
            (project_id+"|"+provider_key+"|"+credential_ref+"|"+account_id).encode()).hexdigest()
        def insert(tx):
            prior=tx.one(
                "SELECT provider_key,credential_ref,enabled FROM external_accounts "
                "WHERE project_id=? AND account_id=?",
                (project_id,account_id))
            if prior is not None:
                if prior["provider_key"] != provider_key or prior["credential_ref"] != credential_ref:
                    raise IntegrationError("conflict","verified account identity already bound")
                if prior["enabled"] != 1:
                    raise IntegrationError("conflict","provider attachment disabled")
                return {"account_id":account_id,"state":"attached"}
            tx.change(
                "INSERT INTO external_accounts(project_id,account_id,provider_key,credential_ref,enabled)"
                " VALUES(?,?,?,?,1)", (project_id,account_id,provider_key,credential_ref))
            return {"account_id":account_id,"state":"attached"}
        try:
            self._writer.command(project_id, "G.attachConnector", request_id, payload, insert)
        except WorkforceFault as exc:
            raise IntegrationError(
                {"unknown":"unknown_effect","conflict":"conflict","invalid":"invalid",
                 "forbidden":"forbidden"}.get(exc.code,"internal"),str(exc)) from exc
