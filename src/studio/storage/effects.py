"""Exact-effect durable intent state machine; remote readback belongs to WG-G."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .engine import ScopedTransaction
from .errors import Conflict, Forbidden, Invalid, UnknownEffect


@dataclass(frozen=True)
class Effect:
    effect_id: str
    subject_type: str
    subject_id: str
    action: str
    effect_digest: str
    snapshot_id: str | None
    provider_key: str
    approval_required: bool
    decision_id: str | None


class EffectJournal:
    @staticmethod
    def stage(tx: ScopedTransaction, effect: Effect) -> None:
        if not all((effect.effect_id, effect.subject_type, effect.subject_id, effect.action, effect.effect_digest, effect.provider_key)):
            raise Invalid("incomplete external effect identity")
        if effect.approval_required and not effect.decision_id:
            raise Forbidden("approval-dependent effect has no exact owner decision")
        # Relational binding of action/subject/effect/snapshot is enforced by FK/triggers.
        tx._execute("INSERT INTO effect_intents(project_id,effect_id,subject_type,subject_id,action,effect_digest,snapshot_id,decision_id,approval_required,state,provider_key,remote_operation_id,request_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'prepared',?,NULL,?)",
                    (tx.project_id, effect.effect_id, effect.subject_type, effect.subject_id, effect.action,
                     effect.effect_digest, effect.snapshot_id, effect.decision_id, int(effect.approval_required),
                     effect.provider_key, tx.request_id))

    @staticmethod
    def dispatch(tx: ScopedTransaction, effect_id: str) -> None:
        """Prepare the already committed intent to send; commit this transition first."""
        cur = tx._execute("UPDATE effect_intents SET state='dispatched' WHERE project_id=? AND effect_id=? "
                          "AND state='prepared' RETURNING 1", (tx.project_id, effect_id))
        if cur.fetchone() is None:
            raise Conflict("effect missing, already dispatched or not prepared")

    @staticmethod
    def report_unknown(tx: ScopedTransaction, effect_id: str) -> None:
        cur = tx._execute("UPDATE effect_intents SET state='unknown' WHERE project_id=? AND effect_id=? "
                          "AND state='dispatched' RETURNING 1", (tx.project_id, effect_id))
        if cur.fetchone() is None:
            raise Conflict("effect cannot be marked unknown")

    @staticmethod
    def record_readback(tx: ScopedTransaction, *, effect_id: str, expected_digest: str,
                        observed_digest: str, remote_operation_id: str, trusted_verified: bool) -> None:
        """WG-G supplies trusted remote readback; never trust raw browser claims.

        C verifies equality with the immutable intent, but the adapter doing remote
        byte verification is the caller's trust boundary.
        """
        if not trusted_verified or expected_digest != observed_digest or not remote_operation_id:
            raise UnknownEffect("remote receipt is not independently verified")
        cur = tx._execute("UPDATE effect_intents SET state='observed',remote_operation_id=? "
                          "WHERE project_id=? AND effect_id=? AND effect_digest=? "
                          "AND state IN ('dispatched','unknown') RETURNING 1",
                          (remote_operation_id, tx.project_id, effect_id, observed_digest))
        if cur.fetchone() is None:
            raise Conflict("effect identity mismatch or non-reconcilable state")

    @staticmethod
    def pending(tx: ScopedTransaction, *, limit: int = 100) -> list[dict[str, Any]]:
        if tx.domain_owner != "WG-C":
            raise Forbidden("effect journal scanning belongs to storage coordinator")
        if not isinstance(limit, int) or not (1 <= limit <= 250):
            raise Invalid("invalid effect scan limit")
        return tx._rows("SELECT * FROM effect_intents WHERE project_id=? AND state IN ('prepared','dispatched','unknown') "
                        "ORDER BY effect_id LIMIT ?", (tx.project_id, limit))
