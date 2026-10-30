"""Trusted J principal provisioner persistence in C's APSW writer.

Only server-side identity verification can invoke this port. A principal's
issuer/subject/kind cannot be rebound by a later credential or other project.
"""
from __future__ import annotations

from studio.api.errors import ApiError
from .workforce import WorkforceStorage


class PrincipalAPSWStore:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("real C APSW writer required for principal registration")
        self._writer = writer

    def ensure_trusted_principal(self, *, principal_id: str,
                                 auth_issuer: str, auth_subject: str, kind: str) -> None:
        if (not all(isinstance(v, str) and v and len(v) <= 512
                    for v in (principal_id, auth_issuer, auth_subject))
                or kind not in ("human", "service")):
            raise ApiError("invalid", "invalid verified principal identity")
        with self._writer._transaction(write=True) as tx:
            existing = tx.one(
                "SELECT auth_issuer,auth_subject,kind FROM principals WHERE principal_id=?",
                (principal_id,))
            if existing is not None:
                if existing != {"auth_issuer":auth_issuer,
                                "auth_subject":auth_subject,"kind":kind}:
                    raise ApiError("conflict", "verified principal already bound to other identity")
                return
            duplicate = tx.one(
                "SELECT principal_id FROM principals WHERE auth_issuer=? AND auth_subject=?",
                (auth_issuer,auth_subject))
            if duplicate is not None:
                raise ApiError("conflict", "verified issuer/subject already assigned")
            tx.change("INSERT INTO principals(principal_id,auth_issuer,auth_subject,kind) "
                      "VALUES(?,?,?,?)",
                      (principal_id,auth_issuer,auth_subject,kind))
