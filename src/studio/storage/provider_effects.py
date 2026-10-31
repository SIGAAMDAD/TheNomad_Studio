"""C-backed G effect reconciliation records with exact trusted destination binding.

Remote identity/path are resolved by the installed provider configuration, not
by browser text or an HTTP request. Effect bytes are verified in WG-G before a
C-serialized observation can advance the durable effect state.
"""
from __future__ import annotations

from collections.abc import Callable

from studio.integrations.common import IntegrationError, identifier
from studio.integrations.effects import EffectIntent, EffectObservation
from .workforce import WorkforceStorage


class EffectAPSWRepository:
    def __init__(self, writer: WorkforceStorage,
                 resolve_target: Callable[[str,str,str,str,str],tuple[str,str]]):
        if not isinstance(writer,WorkforceStorage) or not callable(resolve_target):
            raise TypeError("C APSW writer and authenticated provider binding required")
        self.writer=writer
        self.resolve_target=resolve_target

    def load(self, project_id: str, effect_id: str) -> EffectIntent:
        identifier(project_id)
        identifier(effect_id)
        row=self.writer.read(project_id,lambda tx:tx.one(
            "SELECT provider_key,subject_type,subject_id,action,effect_digest,request_id "
            "FROM effect_intents WHERE project_id=? AND effect_id=?",
            (project_id,effect_id)))
        if row is None:
            raise IntegrationError("missing","external effect does not exist")
        identity,path=self.resolve_target(
            project_id,row["provider_key"],row["subject_type"],row["subject_id"],row["action"])
        if not isinstance(identity,str) or not identity or not isinstance(path,str) or not path:
            raise IntegrationError("capability_unavailable","effect destination is not configured")
        return EffectIntent(effect_id,project_id,row["provider_key"],
                            identity,path,row["effect_digest"],row["request_id"])

    def store_observation(self, project_id: str, observation: EffectObservation,
                          expected_state: str | None) -> None:
        identifier(project_id)
        identifier(observation.effect_id)
        if observation.state not in ("VERIFIED","CONFLICT","UNKNOWN"):
            raise IntegrationError("invalid","unrecognized trusted effect observation")
        with self.writer._transaction(write=True) as tx:
            row=tx.one("SELECT state,effect_digest,remote_operation_id FROM effect_intents "
                       "WHERE project_id=? AND effect_id=?",
                       (project_id,observation.effect_id))
            if row is None:
                raise IntegrationError("missing","effect intent no longer exists")
            state={"prepared":"PENDING","dispatched":"PENDING",
                   "unknown":"UNKNOWN","observed":"VERIFIED","failed":"CONFLICT"}[row["state"]]
            if expected_state is not None and expected_state!=state:
                raise IntegrationError("conflict","effect state changed during reconciliation")
            if observation.state=="UNKNOWN":
                if row["state"] in ("dispatched","unknown"):
                    tx.change("UPDATE effect_intents SET state='unknown' "
                              "WHERE project_id=? AND effect_id=?",
                              (project_id,observation.effect_id))
                return
            if (not observation.remote_identity or not observation.body_sha256):
                raise IntegrationError("invalid","observed effect lacks exact remote receipt")
            if row["remote_operation_id"] not in (None,observation.remote_identity):
                raise IntegrationError("conflict","remote effect identity changed")
            if observation.state=="VERIFIED" and observation.body_sha256!=row["effect_digest"]:
                raise IntegrationError("conflict","verified bytes do not match effect intent")
            if observation.state=="CONFLICT" and observation.body_sha256==row["effect_digest"]:
                raise IntegrationError("invalid","conflict observation matches expected digest")
            if row["state"]=="observed" and observation.state=="CONFLICT":
                raise IntegrationError("conflict","observed successful effect cannot be downgraded")
            result="observed" if observation.state=="VERIFIED" else "failed"
            tx.change("UPDATE effect_intents SET state=?,remote_operation_id=? "
                      "WHERE project_id=? AND effect_id=?",
                      (result,observation.remote_identity,project_id,observation.effect_id))
