"""WG-M integration HTTP-command implementations (WG-J owns HTTP/auth).

C's source_revisions and integration_intents are the authoritative business
state. The local file tree is a recoverable content-addressed cache. The C
adapter must atomically CAS the canonical revision and intent state. It must
never infer the result of GitHub publication from a local file write.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import StudioError
from .intake import Handoff, Roster, digest, is_owned
from .integration import CanonicalTree, IndependentQAVerifier, SourceReadback
from .projects import Actor, request_hash


@dataclass(frozen=True)
class Submission:
    submission_id: str
    project_id: str
    author_shift_id: str
    group: str
    handoff: Handoff
    remote_bytes_verified: bool
    submitted: bool


class VerifiedSubmissionPort(Protocol):
    def load_submission(self, *, project_id: str, submission_id: str) -> Submission | None: ...


@dataclass(frozen=True)
class IntegrationRecord:
    intent_id: str
    project_id: str
    expected_revision_id: str
    staged_revision_id: str
    submission_id: str
    state: str


class IntegrationTransactionPort(Protocol):
    def record_stage(self, *, project_id: str, principal_id: str,
                     request_id: str, request_body_sha256: str,
                     intent_id: str, submission_id: str,
                     expected_revision_id: str, staged_revision_id: str) -> IntegrationRecord: ...
    def load_intent(self, *, project_id: str, intent_id: str) -> IntegrationRecord | None: ...
    def cas_canonical_promotion(self, *, project_id: str, principal_id: str,
                                request_id: str, request_body_sha256: str,
                                intent_id: str, expected_revision_id: str,
                                new_revision_id: str,
                                qa_review_ids: tuple[str, ...]) -> bool: ...
    def canonical_revision(self, *, project_id: str) -> str: ...


class IntegrationService:
    def __init__(self, *, roster: Roster, tree_root: Path,
                 submissions: VerifiedSubmissionPort, state: IntegrationTransactionPort,
                 qa: IndependentQAVerifier, remote: SourceReadback):
        self.roster, self.tree_root = roster, tree_root
        self.submissions, self.state = submissions, state
        self.qa, self.remote = qa, remote

    def _tree(self, project_id: str) -> CanonicalTree:
        return CanonicalTree(self.tree_root, project_id)

    def stageIntegration(self, actor: Actor, project_id: str, request: dict) -> dict:
        actor.require_scope(project_id)
        source = self.submissions.load_submission(
            project_id=project_id, submission_id=request["submission_id"])
        if (source is None or source.project_id != project_id or not source.submitted
                or not source.remote_bytes_verified):
            raise StudioError("missing", "submission lacks verified remote source bytes")
        if source.group not in self.roster.paths or source.handoff.group != source.group:
            raise StudioError("invalid", "submission ownership invalid")
        if source.handoff.plan_sha256 != self.roster.plan_sha256:
            raise StudioError("invalid", "handoff frozen plan differs")
        if any(not is_owned(c.path, self.roster.paths[source.group]) for c in source.handoff.changes):
            raise StudioError("forbidden", "submission contains a foreign-owned path")
        tree = self._tree(project_id)
        expected = request["expected_revision_id"]
        intent_id = digest(request_hash(project_id, request["request_id"],
                                        request["submission_id"]).encode("ascii"))
        existing = self.state.load_intent(project_id=project_id, intent_id=intent_id)
        if existing is not None:
            if (existing.project_id != project_id or existing.submission_id != source.submission_id
                    or existing.expected_revision_id != expected):
                raise StudioError("conflict", "replayed stage does not match original intent")
            candidate = tree.inspect(intent_id)
            if (candidate.revision_id != existing.staged_revision_id or
                    candidate.submission_ids != (source.submission_id,) or
                    candidate.author_ids != (source.author_shift_id,) or
                    candidate.expected_revision_id != expected):
                raise StudioError("conflict", "stage cache replay differs from persisted intent")
            stored = self.state.record_stage(
                project_id=project_id, principal_id=actor.principal_id,
                request_id=request["request_id"],
                request_body_sha256=request_hash(source.submission_id, expected),
                intent_id=intent_id, submission_id=source.submission_id,
                expected_revision_id=expected, staged_revision_id=candidate.revision_id)
            return {"intent_id": stored.intent_id, "state": stored.state}
        if expected != self.state.canonical_revision(project_id=project_id):
            raise StudioError("stale_revision", "authoritative canonical source changed")
        if expected != tree.current_revision():
            raise StudioError("stale_revision", "local canonical source needs recovery")
        candidate = tree.stage(intent_id=intent_id, project_id=project_id,
                               submissions=[(source.submission_id, source.author_shift_id,
                                             source.handoff)])
        record = self.state.record_stage(
            project_id=project_id, principal_id=actor.principal_id,
            request_id=request["request_id"],
            request_body_sha256=request_hash(source.submission_id, expected),
            intent_id=intent_id, submission_id=source.submission_id,
            expected_revision_id=expected, staged_revision_id=candidate.revision_id)
        if (record.project_id != project_id or record.staged_revision_id != candidate.revision_id
                or record.expected_revision_id != expected):
            raise StudioError("invalid", "persisted integration intent differs from verified candidate")
        return {"intent_id": record.intent_id, "state": record.state}

    def inspectIntegration(self, actor: Actor, project_id: str, intent_id: str) -> dict:
        actor.require_scope(project_id)
        record = self.state.load_intent(project_id=project_id, intent_id=intent_id)
        if record is None:
            raise StudioError("missing", "integration intent unavailable")
        return {"intent_id": record.intent_id, "state": record.state}

    def promoteIntegration(self, actor: Actor, project_id: str,
                           intent_id: str, request: dict) -> dict:
        actor.require_scope(project_id)
        record = self.state.load_intent(project_id=project_id, intent_id=intent_id)
        if record is None or record.project_id != project_id or record.state not in ("staged", "qa_checked"):
            raise StudioError("conflict", "integration intent not stageable")
        tree = self._tree(project_id)
        candidate = tree.inspect(intent_id)
        if record.staged_revision_id != candidate.revision_id:
            raise StudioError("invalid", "stored stage digest diverged")
        expected = request["expected_revision_id"]
        if expected != candidate.expected_revision_id or expected != self.state.canonical_revision(project_id=project_id):
            raise StudioError("stale_revision", "canonical pointer advanced")
        reviews = tuple(request["qa_review_ids"])
        if not reviews or not self.qa.verify(
                project_id=project_id, revision_id=candidate.revision_id,
                submission_ids=candidate.submission_ids,
                authors=candidate.author_ids, review_ids=reviews):
            raise StudioError("qa_failed", "independent QA exact revision not verified")
        if not self.remote.verify_exact(project_id=project_id,
                                        revision_id=candidate.revision_id,
                                        tree_digest=candidate.tree_digest, tree=candidate.tree):
            raise StudioError("unknown_effect", "GitHub source readback not verified")
        changed = self.state.cas_canonical_promotion(
            project_id=project_id, principal_id=actor.principal_id,
            request_id=request["request_id"],
            request_body_sha256=request_hash(intent_id, expected, reviews),
            intent_id=intent_id, expected_revision_id=expected,
            new_revision_id=candidate.revision_id, qa_review_ids=reviews)
        if not changed:
            raise StudioError("stale_revision", "authoritative canonical CAS rejected")
        # C is authoritative. If this local mirror update fails, recovery can
        # replay the same verified stage without rolling back the C commit.
        try:
            promoted = tree.promote(intent_id=intent_id, review_ids=reviews,
                                    qa=self.qa, readback=self.remote,
                                    expected_revision_id=expected)
        except Exception as exc:
            raise StudioError("unknown_effect", "authoritative CAS committed; local mirror requires recovery") from exc
        return {"source_revision_id": promoted, "state": "promoted"}

    def reconcileLocalMirror(self, *, project_id: str) -> str:
        """Verify authoritative C revision; no remote action and no chat restart.

        Reconstruct local cache from a previously QA+remote-verified candidate.
        If neither a materialized revision nor matching stage exists, fail closed.
        """
        expected = self.state.canonical_revision(project_id=project_id)
        tree = self._tree(project_id)
        if tree.current_revision() == expected:
            tree.read_revision(expected)
            return expected
        for path in tree.stages.iterdir():
            if not path.is_dir():
                continue
            candidate = tree.inspect(path.name)
            if candidate.revision_id != expected:
                continue
            # Readback proves current canonical bytes, not arbitrary stage data.
            if not self.remote.verify_exact(project_id=project_id,
                                            revision_id=candidate.revision_id,
                                            tree_digest=candidate.tree_digest,
                                            tree=candidate.tree):
                raise StudioError("unknown_effect", "cannot recover local pointer without remote bytes")
            tree.recover_local_pointer(expected, candidate.tree)
            return expected
        raise StudioError("unknown_effect", "no local bytes available to recover canonical revision")
