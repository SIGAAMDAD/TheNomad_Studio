"""Independence-constrained intake, coverage gaps and schedule *suggestions*."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence, Tuple

from .api import valid_digest, valid_id
from .contracts import (CoverageNeed, CoverageSuggestion, Identity, Result,
                        ReviewItem, Reviewer, ReviewSuggestion, ScheduleSuggestion)
from .store import LocalJournal, digest


class WorkforcePlanning:
    def __init__(self, journal: Optional[LocalJournal] = None) -> None:
        self.journal = journal if journal is not None else LocalJournal()

    def intake(self, item: ReviewItem) -> str:
        """Records nonprivate reference metadata, not report text or finding."""
        if (not valid_id(item.intake_id) or not valid_id(item.subject_worker_id) or
            not valid_id(item.origin_principal) or not valid_id(item.category) or
            not valid_id(item.specialty) or not valid_digest(item.evidence_digest) or
            not (0 <= item.priority <= 9) or item.received_at < 0):
            return 'DENIED_INVALID_INTAKE'
        return self.journal.record_intake(item.intake_id, digest(item), item,
                                          item.priority, item.received_at)

    def suggest_review(self, item: ReviewItem, reviewers: Sequence[Reviewer], now: int,
                       prior_review_authors: Iterable[str] = ()) -> ReviewSuggestion:
        """Pure recommendation. Never writes confidential case owner/assignment."""
        excluded = {item.subject_worker_id, item.origin_principal,
                    item.intake_id, *item.excluded_principals, *prior_review_authors}
        eligible = [r for r in reviewers if r.active and r.principal_id not in excluded
                    and valid_id(r.principal_id) and r.training_valid_until > now
                    and item.specialty in r.specialty and not (r.conflicts & excluded)
                    and 0 <= r.active_cases < r.case_capacity]
        if not eligible:
            return ReviewSuggestion(item.intake_id, None, Result.BLOCKED_INDEPENDENCE,
                                    'NO_ELIGIBLE_INDEPENDENT_REVIEWER')
        eligible.sort(key=lambda r: (r.active_cases / r.case_capacity, r.active_cases, r.principal_id))
        return ReviewSuggestion(item.intake_id, eligible[0].principal_id,
                                Result.REVIEW_RECOMMENDED, 'CASE_OWNER_APPROVAL_REQUIRED')

    def backlog(self) -> Tuple[dict, ...]:
        return self.journal.intake_rows()

    def coverage(self, identities: Sequence[Identity], needs: Sequence[CoverageNeed]) -> Tuple[CoverageSuggestion, ...]:
        """Every target must come from caller's independently approved needs."""
        if any(n.required < 0 or not valid_id(n.role_id) or not valid_id(n.department_id) for n in needs):
            raise ValueError('coverage targets require validated policy values')
        seen = set()
        counts = {}
        for i in identities:
            if i.worker_id in seen:
                raise ValueError('duplicate Studio worker identity')
            seen.add(i.worker_id)
            if i.active and not i.retired:
                counts.setdefault((i.role_id, i.department_id), []).append(i.worker_id)
        result = []
        for n in needs:
            ids = tuple(sorted(counts.get((n.role_id, n.department_id), [])))
            result.append(CoverageSuggestion(n.role_id, n.department_id, n.required,
                                             len(ids), max(0, n.required-len(ids)), ids))
        return tuple(result)

    def schedule(self, workers: Sequence[Identity], admitted_slots: Optional[int],
                 capacity_source: str = '') -> ScheduleSuggestion:
        """Owner/provider-supplied capacity, never an invented 500 active chat limit."""
        if admitted_slots is None or admitted_slots < 0 or not valid_id(capacity_source):
            return ScheduleSuggestion(Result.NEEDS_POLICY)
        seen = set()
        eligible = []
        for i in workers:
            if i.worker_id in seen:
                return ScheduleSuggestion(Result.DENIED)
            seen.add(i.worker_id)
            if i.active and not i.retired:
                eligible.append(i.worker_id)
        eligible.sort()
        return ScheduleSuggestion(Result.REVIEW_RECOMMENDED,
                                  tuple(eligible[:admitted_slots]),
                                  max(0, len(eligible)-admitted_slots), capacity_source)
