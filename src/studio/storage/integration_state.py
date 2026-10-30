"""Atomic source-integration intent and canonical-promotion state for WG-M.

Local candidate files and GitHub remote bytes are verified by M/G before the
promotion call. The trusted revision verifier must independently resolve the
candidate's exact Git SHA/tree under the configured project repository.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from collections.abc import Callable

from studio.app.errors import StudioError
from studio.app.integration_commands import IntegrationRecord
from studio.workforce import WorkforceFault
from .workforce import WorkforceStorage


@dataclass(frozen=True)
class VerifiedRevision:
    repo_id: str
    commit_sha: str
    tree_digest: str


class IntegrationAPSWStore:
    def __init__(self, writer: WorkforceStorage,
                 verified_revision: Callable[[str, str], VerifiedRevision]):
        if not isinstance(writer, WorkforceStorage) or not callable(verified_revision):
            raise TypeError("C APSW writer and authenticated G revision verification required")
        self.writer, self.verified_revision = writer, verified_revision

    @staticmethod
    def _record(row):
        return IntegrationRecord(row["intent_id"],row["project_id"],
                                 row["expected_revision_id"],row["staged_revision_id"],
                                 row["submission_id"],row["state"]) if row else None

    @staticmethod
    def _fault(exc):
        return StudioError(
            {"conflict":"conflict","unknown":"unknown_effect","invalid":"invalid",
             "forbidden":"forbidden"}.get(exc.code,"internal"),str(exc))

    @staticmethod
    def _authorized(tx,project_id,principal):
        if not isinstance(principal,str) or not principal:
            raise StudioError("forbidden","authenticated integrator principal required")
        grant=tx.one("SELECT 1 AS ok FROM project_grants g JOIN principals p "
                     "ON p.principal_id=g.principal_id WHERE g.project_id=? "
                     "AND g.principal_id=? AND g.grant_code IN ('owner','integrator','department_head') "
                     "LIMIT 1",(project_id,principal))
        if grant is None:
            raise StudioError("forbidden","principal lacks source integration authority")

    @staticmethod
    def _current(tx,project_id):
        rows=tx.all("SELECT revision_id FROM source_revisions "
                    "WHERE project_id=? AND state='canonical' ORDER BY revision_id LIMIT 2",
                    (project_id,))
        if len(rows)!=1:
            raise StudioError("conflict","project canonical revision is absent or ambiguous")
        return rows[0]["revision_id"]

    def canonical_revision(self, *, project_id: str) -> str:
        return self.writer.read(project_id,lambda tx:self._current(tx,project_id))

    def load_intent(self, *, project_id: str, intent_id: str) -> IntegrationRecord | None:
        return self.writer.read(project_id,lambda tx:self._record(tx.one(
            "SELECT project_id,intent_id,submission_id,expected_revision_id,"
            "staged_revision_id,state FROM integration_intents "
            "WHERE project_id=? AND intent_id=?",(project_id,intent_id))))

    def record_stage(self, *, project_id: str, principal_id: str,
                     request_id: str, request_body_sha256: str,
                     intent_id: str, submission_id: str,
                     expected_revision_id: str, staged_revision_id: str) -> IntegrationRecord:
        bound=sha256(
            "\0".join((request_body_sha256,principal_id,intent_id,submission_id,
                        expected_revision_id,staged_revision_id)).encode()).hexdigest()
        def action(tx):
            self._authorized(tx,project_id,principal_id)
            if self._current(tx,project_id)!=expected_revision_id:
                raise StudioError("stale_revision","canonical pointer changed before staging")
            submission=tx.one("SELECT state FROM submissions WHERE project_id=? AND submission_id=?",
                              (project_id,submission_id))
            if submission is None or submission["state"] not in ("submitted","qa_passed"):
                raise StudioError("missing","submitted original evidence unavailable")
            tx.change("INSERT INTO integration_intents(project_id,intent_id,submission_id,"
                      "expected_revision_id,staged_revision_id,state,request_id) "
                      "VALUES(?,?,?,?,?,'staged',?)",
                      (project_id,intent_id,submission_id,expected_revision_id,staged_revision_id,request_id))
            return {"project_id":project_id,"intent_id":intent_id,"submission_id":submission_id,
                    "expected_revision_id":expected_revision_id,
                    "staged_revision_id":staged_revision_id,"state":"staged"}
        try:
            return self._record(self.writer.command(project_id,"M.stageIntegration",
                                                     request_id,bound,action))
        except WorkforceFault as exc:
            raise self._fault(exc) from exc

    def cas_canonical_promotion(self, *, project_id: str, principal_id: str,
                                request_id: str, request_body_sha256: str,
                                intent_id: str, expected_revision_id: str,
                                new_revision_id: str,
                                qa_review_ids: tuple[str, ...]) -> bool:
        if not qa_review_ids or len(set(qa_review_ids))!=len(qa_review_ids):
            raise StudioError("qa_failed","unique accepted QA reviews are required")
        # G's verifier must re-read the exact staged remote source before any
        # C state mutation. Caller-supplied Git SHAs are never authoritative.
        remote=self.verified_revision(project_id,new_revision_id)
        if not isinstance(remote,VerifiedRevision) or remote.tree_digest!=new_revision_id:
            raise StudioError("unknown_effect","GitHub revision byte evidence does not match")
        if (not isinstance(remote.commit_sha,str) or len(remote.commit_sha)!=40
                or any(c not in "0123456789abcdef" for c in remote.commit_sha)):
            raise StudioError("invalid","unverified Git SHA")
        bound=sha256(
            "\0".join((request_body_sha256,principal_id,intent_id,expected_revision_id,
                        new_revision_id,remote.commit_sha,remote.repo_id,*qa_review_ids)).encode()).hexdigest()

        def action(tx):
            self._authorized(tx,project_id,principal_id)
            if self._current(tx,project_id)!=expected_revision_id:
                return {"changed":False}
            intent=tx.one(
                "SELECT submission_id,staged_revision_id,state FROM integration_intents "
                "WHERE project_id=? AND intent_id=? AND expected_revision_id=?",
                (project_id,intent_id,expected_revision_id))
            if intent is None or intent["state"] not in ("staged","qa_checked"):
                raise StudioError("conflict","integration intent is not stageable")
            if intent["staged_revision_id"]!=new_revision_id:
                raise StudioError("conflict","candidate revision differs from stored stage")
            for review_id in qa_review_ids:
                proof=tx.one(
                    "SELECT q.review_id FROM qa_reviews q "
                    "JOIN qa_independence_proofs p ON p.project_id=q.project_id "
                    "AND p.review_id=q.review_id "
                    "WHERE q.project_id=? AND q.review_id=? AND q.submission_id=? "
                    "AND q.verdict='pass' AND q.evidence_digest IS NOT NULL "
                    "AND p.reviewer_principal_id=q.reviewer_principal_id",
                    (project_id,review_id,intent["submission_id"]))
                if proof is None:
                    raise StudioError("qa_failed","independent QA exact submission proof missing")
            repository=tx.one(
                "SELECT 1 AS valid FROM project_repositories WHERE project_id=? AND repo_id=?",
                (project_id,remote.repo_id))
            if repository is None:
                raise StudioError("forbidden","GitHub verified revision uses unknown project repository")
            existing=tx.one(
                "SELECT repo_id,commit_sha,tree_digest,state FROM source_revisions "
                "WHERE project_id=? AND revision_id=?",
                (project_id,new_revision_id))
            if existing is None:
                tx.change(
                    "INSERT INTO source_revisions(project_id,revision_id,repo_id,commit_sha,"
                    "tree_digest,state) VALUES(?,?,?,?,?,'staged')",
                    (project_id,new_revision_id,remote.repo_id,remote.commit_sha,remote.tree_digest))
            elif (existing["repo_id"],existing["commit_sha"],existing["tree_digest"])!=(
                    remote.repo_id,remote.commit_sha,remote.tree_digest):
                raise StudioError("conflict","remote revision identity conflicts with durable source")
            if tx.change("UPDATE source_revisions SET state='superseded' "
                         "WHERE project_id=? AND revision_id=? AND state='canonical'",
                         (project_id,expected_revision_id))!=1:
                raise StudioError("stale_revision","canonical CAS failed")
            if tx.change("UPDATE source_revisions SET state='canonical' WHERE project_id=? "
                         "AND revision_id=? AND state IN ('staged','superseded')",
                         (project_id,new_revision_id))!=1:
                raise StudioError("conflict","new source cannot become canonical")
            tx.change("UPDATE integration_intents SET state='promoted',new_revision_id=? "
                      "WHERE project_id=? AND intent_id=? AND state IN ('staged','qa_checked')",
                      (new_revision_id,project_id,intent_id))
            tx.change("UPDATE submissions SET state='integrated' "
                      "WHERE project_id=? AND submission_id=? AND state='qa_passed'",
                      (project_id,intent["submission_id"]))
            return {"changed":True}

        try:
            result=self.writer.command(project_id,"M.promoteIntegration",
                                       request_id,bound,action)
        except WorkforceFault as exc:
            raise self._fault(exc) from exc
        return result["changed"]
