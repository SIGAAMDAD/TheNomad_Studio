"""Injected interfaces; no built-in owner identity, encryption or signature authority.

An absent port is unsupported. Demo ports in isolated tests are not real grants.
"""
from __future__ import annotations
from typing import Any, Mapping, Protocol


class AccessPort(Protocol):
    def permits(self, principal: str, operation: str, case_id: str | None = None) -> bool: ...


class OwnerPort(Protocol):
    def verify(self, owner_decision_ref: str, action: str, subject: str,
               policy_revision: int) -> bool: ...


class CustodyPort(Protocol):
    def check_sealed(self, ciphertext_ref: str, ciphertext_sha256: str,
                     reporter: str) -> bool: ...
    def scoped_access(self, case_id: str, investigator: str) -> bool: ...


class EvidencePort(Protocol):
    def verify(self, source_ref: str, content_sha256: str,
               source_revision: str, collector: str) -> bool: ...


class IndependencePort(Protocol):
    def eligible(self, principal: str, case_id: str, subject: str | None,
                 reporter: str, implicated: tuple[str, ...], purpose: str) -> bool: ...


class SignedNoticePort(Protocol):
    def verify(self, signed_object: Mapping[str, Any]) -> bool: ...


class AssignmentPort(Protocol):
    def current_epoch(self, worker_id: str) -> int | None: ...
    def current_lease_epoch(self, worker_id: str, job_id: str) -> int | None: ...


class EnforcementPort(Protocol):
    def apply(self, exact_signed_object: Mapping[str, Any]) -> str: ...
    def readback(self, notice_id: str, action: str, subject: str, epoch: int) -> bool: ...


class ArchivePort(Protocol):
    def attest_manifest(self, manifest: Mapping[str, Any]) -> bool: ...
