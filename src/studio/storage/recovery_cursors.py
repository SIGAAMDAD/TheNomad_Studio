"""C-backed replay cursors and isolated unknown relay-channel holds.

StartupRecovery reads exact GitHub observations via an injected G provider and
projects trusted results through I. C controls cursor CAS and disables channels
whose remote ancestry/bytes can no longer be verified.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from studio.app.errors import StudioError
from studio.app.startup import ChannelCursor
from .workforce import WorkforceStorage


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecoveryCursorAPSWState:
    def __init__(self, writer: WorkforceStorage, *, system_principal_id: str):
        if not isinstance(writer,WorkforceStorage):
            raise TypeError("real APSW writer required for relay recovery")
        if not isinstance(system_principal_id,str) or not system_principal_id:
            raise TypeError("trusted registered system principal required")
        self.writer=writer
        self.system_principal_id=system_principal_id

    def channels(self) -> list[ChannelCursor]:
        with self.writer._lock:
            rows=self.writer._connection.execute(
                "SELECT c.project_id,c.channel_id,r.observed_ref_sha "
                "FROM relay_channels c LEFT JOIN relay_sync_cursors r "
                "ON r.project_id=c.project_id AND r.channel_id=c.channel_id "
                "WHERE c.enabled=1 ORDER BY c.project_id,c.channel_id LIMIT 10001")
            result=[ChannelCursor(*row) for row in rows]
        if len(result)>10000:
            raise StudioError("capability_unavailable","relay channel inventory exceeds safe bound")
        return result

    def outstanding_effects(self, *, project_id: str) -> list[str]:
        rows=self.writer.read(project_id,lambda tx:tx.all(
            "SELECT effect_id FROM effect_intents WHERE project_id=? "
            "AND state IN ('unknown','dispatched') ORDER BY effect_id LIMIT 10001",
            (project_id,)))
        if len(rows)>10000:
            raise StudioError("capability_unavailable","effect inventory exceeds safe bound")
        return [r["effect_id"] for r in rows]

    def persist_verified_cursor(self, *, project_id: str, channel_id: str,
                                expected_ref_sha: str | None,
                                new_ref_sha: str) -> bool:
        def valid(sha):
            return isinstance(sha,str) and len(sha)==40 and all(
                c in "0123456789abcdef" for c in sha)
        if not valid(new_ref_sha) or (
                expected_ref_sha is not None and not valid(expected_ref_sha)):
            raise StudioError("invalid","unverified GitHub ref digest")
        with self.writer._transaction(write=True) as tx:
            channel=tx.one("SELECT enabled FROM relay_channels "
                           "WHERE project_id=? AND channel_id=?",
                           (project_id,channel_id))
            if channel is None or channel["enabled"]!=1:
                return False
            prev=tx.one("SELECT observed_ref_sha FROM relay_sync_cursors "
                        "WHERE project_id=? AND channel_id=?",
                        (project_id,channel_id))
            if prev is None:
                if expected_ref_sha is not None:
                    return False
                tx.change("INSERT INTO relay_sync_cursors(project_id,channel_id,"
                          "observed_ref_sha,observed_at) VALUES(?,?,?,?)",
                          (project_id,channel_id,new_ref_sha,utc_now()))
                return True
            if prev["observed_ref_sha"]!=expected_ref_sha:
                return False
            return tx.change(
                "UPDATE relay_sync_cursors SET observed_ref_sha=?,observed_at=? "
                "WHERE project_id=? AND channel_id=? AND observed_ref_sha=?",
                (new_ref_sha,utc_now(),project_id,channel_id,expected_ref_sha))==1

    def hold_channel_unknown(self, *, project_id: str, channel_id: str,
                             reason: str) -> None:
        if not isinstance(reason,str) or not reason:
            raise StudioError("invalid","unknown-channel reason required")
        with self.writer._transaction(write=True) as tx:
            principal=tx.one("SELECT kind FROM principals WHERE principal_id=?",
                             (self.system_principal_id,))
            if principal is None or principal["kind"]!="service":
                raise StudioError("forbidden","configured recovery principal is not trusted")
            # Disabling an uncertain channel prevents any future automatic
            # deliveries until a verified owner action restores the binding.
            row=tx.one("SELECT enabled FROM relay_channels "
                       "WHERE project_id=? AND channel_id=?",
                       (project_id,channel_id))
            if row is None:
                raise StudioError("missing","relay channel does not exist")
            if row["enabled"]!=1:
                return
            tx.change("UPDATE relay_channels SET enabled=0 "
                      "WHERE project_id=? AND channel_id=? AND enabled=1",
                      (project_id,channel_id))
            event_seq=tx.one("SELECT COALESCE(MAX(event_seq),0)+1 AS n "
                             "FROM audit_events WHERE project_id=?", (project_id,))["n"]
            digest=hashlib.sha256(reason.encode("utf-8")).hexdigest()
            tx.change(
                "INSERT INTO audit_events(project_id,event_seq,event_type,"
                "subject_type,subject_id,payload_digest,actor_principal_id) "
                "VALUES(?,?,'relay.channel_held_unknown','relay',?,?,?)",
                (project_id,event_seq,channel_id,digest,self.system_principal_id))


class StartupRecoveryBuilder:
    """Deploy real C and G/I ports together, without starting any browser chat."""

    def __init__(self, *, system_principal_id: str, github, projector):
        if not all((system_principal_id,github,projector)):
            raise TypeError("trusted recovery identity, G reader and I projection required")
        self.system_principal_id=system_principal_id
        self.github=github
        self.projector=projector

    def for_writer(self, writer: WorkforceStorage, _root):
        from studio.app.startup import StartupRecovery
        state=RecoveryCursorAPSWState(writer,
             system_principal_id=self.system_principal_id)
        return StartupRecovery(state,self.github,self.projector)
