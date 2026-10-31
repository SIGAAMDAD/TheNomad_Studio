"""Actual C/J/E/I evidence ports for production coordination.

The authorization and evidence paths read current durable state. GitHub commands
and remote execution cannot be inferred from scheduled assignments or elapsed
time. These gates are wired as trusted ports, never exposed to worker SQL.
"""
from __future__ import annotations

from typing import Any, Mapping

from studio.production.service import ProductionFault
from studio.storage.workforce import WorkforceStorage


class CurrentProductionAuthorization:
    def __init__(self, writer: WorkforceStorage, *, owner_operations: frozenset[str]):
        if not isinstance(writer, WorkforceStorage):
            raise TypeError("C-backed production authorization requires APSW")
        self.writer=writer
        self.owner_operations=owner_operations

    def require(self, actor: Mapping[str, Any], project_id: str,
                operation: str, subject_id: str = "") -> None:
        if (not isinstance(actor, Mapping)
                or not all(isinstance(actor.get(k),str) and actor[k] for k in
                           ("principal_id","issuer","trusted_proof_ref"))
                or not isinstance(actor.get("allowed_project_ids"),(list,tuple,set,frozenset))
                or project_id not in actor["allowed_project_ids"]):
            raise ProductionFault("forbidden","authenticated D project actor required")
        owner=operation in self.owner_operations
        def check(tx):
            row=tx.one("SELECT p.kind,g.grant_code FROM project_grants g "
                       "JOIN principals p ON p.principal_id=g.principal_id "
                       "WHERE g.project_id=? AND g.principal_id=? AND p.auth_issuer=? "
                       "AND (?=0 OR (g.grant_code='owner' AND p.kind='human')) "
                       "ORDER BY g.grant_code LIMIT 1",
                       (project_id,actor["principal_id"],actor["issuer"],int(owner)))
            if row is None or (owner and actor.get("owner_verified") is not True):
                raise ProductionFault("requires_owner" if owner else "forbidden",
                                      "current production authorization missing")
        self.writer.read(project_id,check)


class VerifiedProjectBriefs:
    def __init__(self, writer: WorkforceStorage, original):
        if not isinstance(writer,WorkforceStorage) or not callable(getattr(original,"get",None)):
            raise TypeError("verified C/G bytes required for production briefs")
        self.writer,self.original=writer,original

    def get_verified(self, *, project_id: str, content_sha256: str) -> bytes:
        row=self.writer.read(project_id,lambda tx:tx.one(
            "SELECT 1 AS ok FROM project_blobs p JOIN blobs b "
            "ON b.content_digest=p.content_digest "
            "WHERE p.project_id=? AND p.content_digest=? "
            "AND p.purpose='feature-brief' AND b.durability='verified'",
            (project_id,content_sha256)))
        if row is None:
            raise ProductionFault("forbidden","original project feature brief not verified")
        body=self.original.get(project_id=project_id,digest=content_sha256)
        import hashlib
        if hashlib.sha256(body).hexdigest()!=content_sha256:
            raise ProductionFault("conflict","original feature brief was modified")
        return body


class IndependentQAAcceptance:
    """Only exact reviewed source, acceptance and E receipt authorizes acceptance."""
    @staticmethod
    def _proof(tx, *, project_id: str, task_id: str,
               lease_generation: int, evidence_digest: str,
               verdict: str) -> None:
        rows=tx.all(
            "SELECT q.review_id FROM qa_reviews q "
            "JOIN qa_independence_proofs p ON p.project_id=q.project_id "
            "AND p.review_id=q.review_id "
            "JOIN submissions s ON s.project_id=q.project_id "
            "AND s.submission_id=q.submission_id "
            "JOIN tasks t ON t.project_id=s.project_id AND t.task_id=s.task_id "
            "WHERE q.project_id=? AND s.task_id=? AND s.lease_generation=? "
            "AND q.verdict=? AND q.evidence_digest=? "
            "AND q.acceptance_digest=t.acceptance_digest "
            "AND q.author_shift_id=s.shift_id AND q.source_revision_id IS s.source_revision_id "
            "AND p.author_shift_id=s.shift_id AND p.reviewer_principal_id=q.reviewer_principal_id "
            "AND EXISTS(SELECT 1 FROM qa_receipts r WHERE r.project_id=q.project_id "
            "AND r.review_id=q.review_id) "
            "ORDER BY q.review_id LIMIT 1",
            (project_id,task_id,lease_generation,verdict,evidence_digest))
        if not rows:
            raise ProductionFault("qa_failed","independent exact E QA evidence unavailable")

    def require_accepted(self, tx, *, project_id: str, task_id: str,
                         lease_generation: int, evidence_digest: str) -> None:
        self._proof(tx,project_id=project_id,task_id=task_id,
                    lease_generation=lease_generation,evidence_digest=evidence_digest,
                    verdict="pass")

    def require_failed(self, tx, *, project_id: str, task_id: str,
                       evidence_digest: str) -> None:
        rows=tx.all(
            "SELECT q.review_id FROM qa_reviews q JOIN submissions s "
            "ON s.project_id=q.project_id AND s.submission_id=q.submission_id "
            "JOIN qa_independence_proofs p ON p.project_id=q.project_id "
            "AND p.review_id=q.review_id AND p.author_shift_id=s.shift_id "
            "WHERE s.project_id=? AND s.task_id=? AND q.verdict='fail' "
            "AND q.evidence_digest=? AND EXISTS(SELECT 1 FROM qa_receipts r "
            "WHERE r.project_id=q.project_id AND r.review_id=q.review_id) "
            "ORDER BY q.review_id LIMIT 1",
            (project_id,task_id,evidence_digest))
        if not rows:
            raise ProductionFault("qa_failed","independent QA failure record unavailable")


class VerifiedShiftTaskProgress:
    """Require actual durable I/G receipt or E review, never task-state self-claim."""
    def require_stage(self, tx, *, project_id: str, task_id: str,
                      lease_generation: int, next_state: str,
                      evidence_digest: str) -> None:
        if next_state=="running":
            row=tx.one(
                "SELECT sh.hello_digest FROM shift_assignments a "
                "JOIN worker_shifts sh ON sh.project_id=a.project_id "
                "AND sh.shift_id=a.shift_id AND sh.worker_id=a.worker_id "
                "WHERE a.project_id=? AND a.task_id=? AND a.lease_generation=? "
                "AND sh.state='active' AND sh.hello_commit_sha IS NOT NULL",
                (project_id,task_id,lease_generation))
            if row is None or row["hello_digest"]!=evidence_digest:
                raise ProductionFault("conflict","verified shift HELLO evidence missing")
            return

        if next_state in ("submitted","failed","blocked","ready"):
            accepted_kinds={"submitted":("completed",),
                            "failed":("failed",),
                            "blocked":("blocked",),"ready":("stopped",)}
            kinds=accepted_kinds[next_state]
            rows=tx.all(
                "SELECT r.kind FROM command_receipts r JOIN command_outbox c "
                "ON c.project_id=r.project_id AND c.command_id=r.command_id "
                "JOIN shift_assignments a ON a.project_id=c.project_id "
                "AND a.assignment_id=c.assignment_id "
                "WHERE a.project_id=? AND a.task_id=? AND a.lease_generation=? "
                "AND r.result_digest=? AND r.remote_commit_sha IS NOT NULL",
                (project_id,task_id,lease_generation,evidence_digest))
            if any(row["kind"] in kinds for row in rows):
                return

        if next_state=="review":
            row=tx.one(
                "SELECT 1 AS ok FROM submissions s JOIN qa_reviews q "
                "ON q.project_id=s.project_id AND q.submission_id=s.submission_id "
                "WHERE s.project_id=? AND s.task_id=? AND s.lease_generation=? "
                "AND q.verdict IN ('pending','pass','fail') "
                "AND q.evidence_digest=? LIMIT 1",
                (project_id,task_id,lease_generation,evidence_digest))
            if row is not None:
                return

        raise ProductionFault("conflict","verified shift/QA progress evidence unavailable")


class CurrentProductionAuthorizationBuilder:
    """Deploy-time approved owner-only operation set; J/C proves each grant."""
    def __init__(self, owner_operations: frozenset[str]):
        if not isinstance(owner_operations, frozenset) or not all(
                isinstance(s, str) and s for s in owner_operations):
            raise TypeError("explicit immutable owner-operation policy required")
        self.owner_operations=owner_operations

    def for_writer(self, writer: WorkforceStorage, _root):
        return CurrentProductionAuthorization(writer,
            owner_operations=self.owner_operations)


class VerifiedProjectBriefsBuilder:
    """Use real original G physical bytes from the production C writer."""
    def for_writer(self, writer: WorkforceStorage, root):
        from studio.storage.artifact_catalog import ArtifactAPSWCatalog
        return VerifiedProjectBriefs(writer, ArtifactAPSWCatalog(writer,root))
