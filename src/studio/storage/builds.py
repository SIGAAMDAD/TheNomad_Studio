"""C-backed GitHub Actions build dispatch journal.

The intent and queued CI record commit before dispatch. First-claim is a durable
single-writer CAS, so retrying an uncertain dispatch cannot duplicate builds.
Only exact, correlated GitHub Actions observations may change the CI result.
"""
from __future__ import annotations

import hashlib

from studio.integrations.api_ports import PreparedBuild
from studio.integrations.ci import BuildObservation
from studio.integrations.common import IntegrationError, git_sha, identifier
from studio.workforce import WorkforceFault
from .workforce import WorkforceStorage


class BuildAPSWRepository:
    def __init__(self, writer: WorkforceStorage):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("real C APSW writer required for build dispatch")
        self.writer=writer

    @staticmethod
    def _id(prefix: str, *values: str) -> str:
        return prefix + hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()[:40]

    def prepare(self, *, project_id: str, request_id: str,
                source_revision_id: str, build_target: str) -> PreparedBuild:
        for item in (project_id,request_id,source_revision_id,build_target):
            identifier(item)
        intent_id=self._id("build_intent_",project_id,request_id)
        effect_id=self._id("build_effect_",project_id,request_id)
        ci_id=self._id("ci_",project_id,request_id)
        digest=hashlib.sha256(("\0".join((project_id,source_revision_id,build_target))).encode()).hexdigest()

        def action(tx):
            source=tx.one(
                "SELECT commit_sha,state FROM source_revisions "
                "WHERE project_id=? AND revision_id=?", (project_id,source_revision_id))
            if source is None or source["state"]!="canonical":
                raise IntegrationError("stale_revision","build source is not canonical")
            sha=git_sha(source["commit_sha"])
            tx.change(
                "INSERT INTO effect_intents(project_id,effect_id,subject_type,subject_id,"
                "action,effect_digest,approval_required,state,provider_key,request_id)"
                "VALUES(?,?,'build',?,'build.dispatch',?,0,'prepared','github',?)",
                (project_id,effect_id,ci_id,digest,"G.build."+request_id))
            # An empty remote ID is explicitly unobserved, never a fabricated
            # GitHub Actions run identifier. The exact ID is stored on readback.
            tx.change(
                "INSERT INTO ci_runs(project_id,ci_id,source_revision_id,provider_key,"
                "remote_run_id,status) VALUES(?,?,?,'github','','queued')",
                (project_id,ci_id,source_revision_id))
            return {"project_id":project_id,"ci_id":ci_id,"effect_id":effect_id,
                    "source_sha":sha,"target_id":build_target,"intent_id":intent_id}
        try:
            value=self.writer.command(project_id,"G.runBuild",request_id,digest,action)
        except WorkforceFault as exc:
            raise IntegrationError({"unknown":"unknown_effect","conflict":"conflict",
                                     "invalid":"invalid"}.get(exc.code,"internal"),str(exc)) from exc
        return PreparedBuild(**value)

    def claim_first_dispatch(self, *, project_id: str, effect_id: str) -> bool:
        with self.writer._transaction(write=True) as tx:
            existing=tx.one(
                "SELECT state FROM effect_intents WHERE project_id=? AND effect_id=? "
                "AND action='build.dispatch'",(project_id,effect_id))
            if existing is None:
                raise IntegrationError("missing","build intent does not exist")
            if existing["state"]!="prepared":
                return False
            return tx.change(
                "UPDATE effect_intents SET state='dispatched' "
                "WHERE project_id=? AND effect_id=? AND state='prepared'",
                (project_id,effect_id))==1

    def observe(self, *, project_id: str, ci_id: str,
                run: BuildObservation | None) -> None:
        with self.writer._transaction(write=True) as tx:
            record=tx.one(
                "SELECT c.source_revision_id,c.remote_run_id,e.effect_id,e.state,"
                "s.commit_sha FROM ci_runs c "
                "JOIN source_revisions s ON s.project_id=c.project_id "
                "AND s.revision_id=c.source_revision_id "
                "JOIN effect_intents e ON e.project_id=c.project_id "
                "AND e.subject_id=c.ci_id AND e.action='build.dispatch' "
                "WHERE c.project_id=? AND c.ci_id=?",
                (project_id,ci_id))
            if record is None:
                raise IntegrationError("missing","queued build was not found")
            if record["state"]=="prepared":
                raise IntegrationError("conflict","build has not been dispatched")
            if run is None:
                tx.change("UPDATE ci_runs SET status='unknown' "
                          "WHERE project_id=? AND ci_id=? AND status NOT IN ('passed','failed')",
                          (project_id,ci_id))
                return
            if (type(run.run_id) is not int or run.run_id<1 or
                    git_sha(run.head_sha)!=git_sha(record["commit_sha"])):
                raise IntegrationError("conflict","GitHub run is not bound to exact source")
            remote=str(run.run_id)
            if record["remote_run_id"] not in ("",remote):
                raise IntegrationError("conflict","ambiguous remote build observation")
            status=("passed" if run.status=="completed" and run.conclusion=="success"
                    else "failed" if run.status=="completed" and run.conclusion is not None
                    else "running")
            tx.change(
                "UPDATE ci_runs SET remote_run_id=?,status=? WHERE project_id=? AND ci_id=?",
                (remote,status,project_id,ci_id))
            tx.change(
                "UPDATE effect_intents SET state='observed',remote_operation_id=? "
                "WHERE project_id=? AND effect_id=? AND state IN ('dispatched','unknown','observed')",
                (remote,project_id,record["effect_id"]))
