"""A04: trusted current-head proof for an exact SQLite CLAIM command.

Routes are server-owned and provider readback is mandatory. A worker cannot
select a Git repository or branch by supplying fields in its own command.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Mapping

from .gateway import AdmissionError

_SHA = re.compile(r'^[0-9a-f]{40}$')


@dataclass(frozen=True)
class SourceRoute:
    job_id: str
    repo_id: str
    work_branch: str
    source_base_sha: str
    principal_id: str
    worker_id: str
    grant_id: str
    assignment_epoch: int
    authority_revision: str


@dataclass(frozen=True)
class SourceProof:
    route: SourceRoute
    observed_head_sha: str
    checked_at: float


class SourceBaseVerifier:
    """A privately configured GitSource and route registry; no worker input to routes."""
    def __init__(self, git, routes: Mapping[str, SourceRoute], *, clock=time.monotonic, ttl_seconds=10.0):
        if git is None or not routes or not 0 < ttl_seconds <= 30:
            raise ValueError('trusted source provider, routes and short proof lifetime required')
        if any(key != route.job_id or not _SHA.fullmatch(route.source_base_sha)
               for key, route in routes.items()):
            raise ValueError('invalid route registry')
        self._git = git
        self._routes = dict(routes)
        self._clock = clock
        self._ttl = ttl_seconds

    def verify(self, command: dict, principal_id: str) -> SourceProof:
        """Read actual Git HEAD, rejecting route, identity, epoch and base mismatch."""
        try:
            route = self._routes[command['job_id']]
            if (principal_id != route.principal_id or command['worker_id'] != route.worker_id
                    or command['authority_revision'] != route.authority_revision
                    or command['assignment_epoch'] != route.assignment_epoch
                    or command['payload']['grant_id'] != route.grant_id
                    or command['payload']['source_base_sha'] != route.source_base_sha):
                raise AdmissionError('REBASE_REQUIRED')
        except (LookupError, KeyError, TypeError, AttributeError):
            raise AdmissionError('REBASE_REQUIRED') from None
        try:
            snapshot = self._git.snapshot(route.repo_id, route.work_branch)
        except Exception:
            raise AdmissionError('GATEWAY_UNAVAILABLE') from None
        if (snapshot.repo_id != route.repo_id or snapshot.branch != route.work_branch
                or snapshot.head_sha != route.source_base_sha):
            raise AdmissionError('REBASE_REQUIRED')
        return SourceProof(route, snapshot.head_sha, self._clock())

    def confirm_in_claim(self, command: dict, principal_id: str, proof: SourceProof,
                         *, db_repo: str, db_base: str, db_epoch: int) -> None:
        if not isinstance(proof, SourceProof) or self._clock() - proof.checked_at > self._ttl:
            raise AdmissionError('REBASE_REQUIRED')
        if proof.checked_at > self._clock() or proof.route.repo_id != db_repo or proof.route.source_base_sha != db_base:
            raise AdmissionError('REBASE_REQUIRED')
        if proof.route.assignment_epoch != db_epoch or proof.observed_head_sha != db_base:
            raise AdmissionError('REBASE_REQUIRED')
        fresh = self.verify(command, principal_id)
        if fresh.route != proof.route or fresh.observed_head_sha != proof.observed_head_sha:
            raise AdmissionError('REBASE_REQUIRED')
