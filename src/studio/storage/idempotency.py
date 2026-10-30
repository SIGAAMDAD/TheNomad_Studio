"""Request/body digest idempotency using the frozen idempotency table."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .engine import ScopedTransaction
from .errors import Conflict, Invalid


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def request_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class RequestStatus:
    state: str
    result_digest: str | None
    first_claim: bool


class IdempotencyJournal:
    """Operations execute within the caller's transaction; no remote write here."""

    @staticmethod
    def claim(tx: ScopedTransaction, action: str, payload: Any) -> RequestStatus:
        if not action or not isinstance(action, str):
            raise Invalid("action is required")
        digest = request_digest(payload)
        found = tx._execute(
            "SELECT payload_digest,status,result_digest FROM idempotency "
            "WHERE project_id=? AND action_owner=? AND request_id=?",
            (tx.project_id, action, tx.request_id),
        ).fetchone()
        if found is not None:
            old_digest, state, result = found
            if old_digest != digest:
                raise Conflict("request ID already bound to another request body")
            return RequestStatus(state, result, False)
        tx._execute("INSERT INTO idempotency(project_id,action_owner,request_id,payload_digest,status) "
                    "VALUES (?,?,?,?,'started')", (tx.project_id, action, tx.request_id, digest))
        return RequestStatus("started", None, True)

    @staticmethod
    def finish(tx: ScopedTransaction, action: str, result_digest: str) -> None:
        if not isinstance(result_digest, str) or len(result_digest) != 64 or any(c not in "0123456789abcdef" for c in result_digest):
            raise Invalid("result digest must be lowercase SHA-256")
        cur = tx._execute("UPDATE idempotency SET status='finished',result_digest=? "
                          "WHERE project_id=? AND action_owner=? AND request_id=? "
                          "AND status='started' RETURNING 1",
                          (result_digest, tx.project_id, action, tx.request_id))
        if cur.fetchone() is None:
            raise Conflict("idempotency claim not active")

    @staticmethod
    def mark_unknown(tx: ScopedTransaction, action: str) -> None:
        cur = tx._execute("UPDATE idempotency SET status='unknown' WHERE project_id=? "
                          "AND action_owner=? AND request_id=? AND status='started' RETURNING 1",
                          (tx.project_id, action, tx.request_id))
        if cur.fetchone() is None:
            raise Conflict("cannot mark nonexistent/completed request unknown")

    @staticmethod
    def replay_result(tx: ScopedTransaction, action: str, payload: Any, blob_store: Any) -> bytes | None:
        """Return persisted canonical response bytes or None for a first claim.

        Caller stores response in ContentAddressedStore before calling finish.
        Unresolved started/unknown requests must be reconciled, never retried blindly.
        """
        status = IdempotencyJournal.claim(tx, action, payload)
        if status.first_claim:
            return None
        if status.state != "finished" or status.result_digest is None:
            raise Conflict("request is pending or has unknown effect")
        return blob_store.read_digest(status.result_digest)


@dataclass(frozen=True)
class BootstrapStatus:
    state: str
    result_project_id: str | None
    first_claim: bool


class BootstrapJournal:
    """First-project create uses the separate frozen principal/request key space."""

    @staticmethod
    def claim(tx: ScopedTransaction, payload: Any) -> BootstrapStatus:
        if tx.domain_owner not in ("WG-M", "WG-C"):
            from .errors import Forbidden
            raise Forbidden("bootstrap journal belongs to Studio composition")
        digest = request_digest(payload)
        row = tx._execute("SELECT payload_digest,status,result_project_id FROM bootstrap_idempotency "
                          "WHERE principal_id=? AND request_id=?", (tx.actor.principal_id, tx.request_id)).fetchone()
        if row is not None:
            if digest != row[0]:
                raise Conflict("bootstrap request ID reused with different body")
            return BootstrapStatus(row[1], row[2], False)
        tx._execute("INSERT INTO bootstrap_idempotency(principal_id,request_id,payload_digest,status) VALUES (?,?,?,'started')",
                    (tx.actor.principal_id, tx.request_id, digest))
        return BootstrapStatus("started", None, True)

    @staticmethod
    def finish(tx: ScopedTransaction, result_project_id: str) -> None:
        if result_project_id != tx.project_id:
            from .errors import Forbidden
            raise Forbidden("bootstrap result project differs from request scope")
        cur = tx._execute("UPDATE bootstrap_idempotency SET status='finished',result_project_id=? "
                          "WHERE principal_id=? AND request_id=? AND status='started' RETURNING 1",
                          (result_project_id, tx.actor.principal_id, tx.request_id))
        if cur.fetchone() is None:
            raise Conflict("bootstrap request not in started state")
