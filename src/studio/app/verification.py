"""Adapters that turn externally attested QA and source data into exact-byte gates.

The provider ports MUST be backed by WG-E/L and WG-G production readback,
not chat-authored status strings. In particular, any unavailable query fails
closed instead of reporting PASS.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .intake import digest


@dataclass(frozen=True)
class QARecord:
    review_id: str
    project_id: str
    source_revision_id: str
    submission_id: str
    author_session_id: str
    reviewer_principal_id: str
    verdict: str
    evidence_digest: str
    independence_verified: bool
    evidence_bytes_verified: bool


class QAReceiptPort(Protocol):
    def get_review(self, *, project_id: str, review_id: str) -> QARecord | None: ...


class ExactRemoteBytesPort(Protocol):
    def read_revision(self, *, project_id: str, revision_id: str) -> dict[str, bytes] | None: ...


class IndependentReceiptGate:
    def __init__(self, qa_records: QAReceiptPort):
        self.qa_records = qa_records

    def verify(self, *, project_id: str, revision_id: str,
               submission_ids: tuple[str, ...], authors: tuple[str, ...],
               review_ids: tuple[str, ...]) -> bool:
        if not review_ids or not submission_ids or len(submission_ids) != len(authors):
            return False
        if len(set(review_ids)) != len(review_ids):
            return False
        expected = dict(zip(submission_ids, authors, strict=True))
        approved: set[str] = set()
        for review_id in review_ids:
            review = self.qa_records.get_review(project_id=project_id, review_id=review_id)
            if (review is None or review.project_id != project_id or
                    review.review_id != review_id or
                    review.source_revision_id != revision_id or
                    review.submission_id not in expected or
                    review.author_session_id != expected[review.submission_id] or
                    review.reviewer_principal_id == review.author_session_id or
                    review.verdict != "pass" or not review.independence_verified or
                    not review.evidence_bytes_verified or
                    not review.evidence_digest or review.submission_id in approved):
                return False
            approved.add(review.submission_id)
        return approved == set(submission_ids)


class RemoteTreeReadbackGate:
    def __init__(self, remote: ExactRemoteBytesPort):
        self.remote = remote

    def verify_exact(self, *, project_id: str, revision_id: str,
                     tree_digest: str, tree: dict[str, bytes]) -> bool:
        # GitHub remote source identity must be checked by WG-G before supplying
        # a verified revision here; local comparisons ensure exact content.
        from .integration import tree_digest as calculate_tree_digest
        if tree_digest != revision_id or calculate_tree_digest(tree) != tree_digest:
            return False
        remote_tree = self.remote.read_revision(project_id=project_id,
                                                revision_id=revision_id)
        return remote_tree is not None and remote_tree == tree
