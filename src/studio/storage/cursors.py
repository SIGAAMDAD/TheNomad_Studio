"""Relay cursor durable advancement after externally verified paginated traversal."""
from __future__ import annotations

from .engine import ScopedTransaction
from .errors import Conflict, Forbidden, Invalid, UnknownEffect


class RelayCursorJournal:
    @staticmethod
    def checkpoint(tx: ScopedTransaction, *, channel_id: str, prior_ref_sha: str | None,
                   verified_ref_sha: str, observed_at: str, traversal_complete: bool,
                   ancestry_verified: bool, trusted_readback: bool) -> None:
        if tx.domain_owner != "WG-I":
            raise Forbidden("relay cursor belongs to WG-I")
        if not all(isinstance(s, str) and s for s in (channel_id, verified_ref_sha, observed_at)):
            raise Invalid("channel/ref/time required")
        if not traversal_complete or not ancestry_verified or not trusted_readback:
            raise UnknownEffect("cursor not advanced without complete trusted GitHub scan and ancestry")
        current = tx._execute("SELECT observed_ref_sha FROM relay_sync_cursors WHERE project_id=? AND channel_id=?",
                              (tx.project_id, channel_id)).fetchone()
        if (current[0] if current else None) != prior_ref_sha:
            raise Conflict("relay cursor ref changed; re-scan required")
        if current is None:
            tx._execute("INSERT INTO relay_sync_cursors(project_id,channel_id,observed_ref_sha,observed_at) VALUES (?,?,?,?)",
                        (tx.project_id, channel_id, verified_ref_sha, observed_at))
        else:
            cur = tx._execute("UPDATE relay_sync_cursors SET observed_ref_sha=?, observed_at=? "
                              "WHERE project_id=? AND channel_id=? AND observed_ref_sha=? RETURNING 1",
                              (verified_ref_sha, observed_at, tx.project_id, channel_id, prior_ref_sha))
            if cur.fetchone() is None:
                raise Conflict("cursor compare-and-swap failed")

    @staticmethod
    def resume_sha(tx: ScopedTransaction, *, channel_id: str) -> str | None:
        if tx.domain_owner != "WG-I":
            raise Forbidden("relay cursor belongs to WG-I")
        row = tx._execute("SELECT observed_ref_sha FROM relay_sync_cursors WHERE project_id=? AND channel_id=?",
                          (tx.project_id, channel_id)).fetchone()
        return row[0] if row else None
