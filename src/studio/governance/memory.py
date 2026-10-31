"""Functional transactional reference adapter for deterministic unit tests.

WG-C owns the APSW production adapter; this adapter never claims production durability.
"""
from __future__ import annotations
import copy
import hashlib
import threading
from contextlib import contextmanager
from typing import Any, Iterator
from .model import GovernanceError, Principal, ProjectState


class MemoryTransactions:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[str, ProjectState] = {}

    def create_project(self, project_id: str, *, owner_id: str) -> None:
        with self._lock:
            if project_id in self._states:
                raise GovernanceError('conflict', 'project already exists')
            s = ProjectState(project_id)
            s.grants.add((owner_id, 'owner'))
            self._states[project_id] = s

    @contextmanager
    def project(self, project_id: str) -> Iterator[ProjectState]:
        with self._lock:
            existing = self._states.get(project_id)
            if existing is None:
                raise GovernanceError('missing', 'project not found')
            working = copy.deepcopy(existing)
            try:
                yield working
            except BaseException:
                raise
            else:
                self._states[project_id] = working


class MemoryBlobSource:
    """Tests real SHA readback and explicit project/purpose association."""
    def __init__(self) -> None:
        self._bytes: dict[str, bytes] = {}
        self._associations: set[tuple[str, str, str]] = set()

    def put_verified(self, project_id: str, data: bytes, purpose: str) -> str:
        digest = hashlib.sha256(data).hexdigest()
        self._bytes[digest] = data
        self._associations.add((project_id, digest, purpose))
        return digest

    def corrupt(self, digest: str, replacement: bytes) -> None:
        self._bytes[digest] = replacement

    def read_verified(self, project_id: str, digest: str, purpose: str) -> bytes:
        if (project_id, digest, purpose) not in self._associations:
            raise GovernanceError('forbidden', 'blob is not verified for project and purpose')
        data = self._bytes[digest]
        if hashlib.sha256(data).hexdigest() != digest:
            raise GovernanceError('conflict', 'physical blob checksum mismatch')
        return data


class MemoryIdentity:
    """Test-only principal proof registry; no caller-controlled owner switch."""
    def __init__(self) -> None:
        self._sessions: dict[str, Principal] = {}

    def provision(self, credential: str, principal: Principal) -> None:
        self._sessions[credential] = principal

    def authenticate(self, actor: dict[str, Any]) -> Principal:
        cred = actor.get('trusted_proof_ref')
        match = self._sessions.get(cred) if isinstance(cred, str) else None
        if match is None or actor.get('principal_id') != match.principal_id or actor.get('issuer') != match.issuer:
            raise GovernanceError('forbidden', 'actor credential is not verified')
        if not isinstance(actor.get('allowed_project_ids'), list):
            raise GovernanceError('forbidden', 'invalid actor project claims')
        return match


class MemorySubjectCatalog:
    def __init__(self) -> None:
        self.subjects: set[tuple[str, str, str]] = set()

    def exists(self, project_id: str, subject_type: str, subject_id: str) -> bool:
        return (project_id, subject_type, subject_id) in self.subjects


class MemoryImpactCatalog:
    def __init__(self) -> None:
        self.references: dict[tuple[str, str], set[str]] = {}

    def referenced_tasks(self, project_id: str, clause_keys: set[str]) -> set[str]:
        result: set[str] = set()
        for k in clause_keys:
            result.update(self.references.get((project_id, k), set()))
        return result
