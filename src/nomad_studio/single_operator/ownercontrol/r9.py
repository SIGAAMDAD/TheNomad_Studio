"""New-only, trusted owner proof gateway for CAB-D protected mutations.

A caller-supplied Decision is just a claim. CAB-H's IdentityPort and a separate
trusted attestation reader must independently bind the proof to an action,
project, digest, owner identity, expiration and current project authority epoch.
"""
from __future__ import annotations

from typing import Any
from nomad_studio.bibles.model import BibleError, identifier


class OwnerProofGateway:
    def __init__(self, identity_port: Any, attestation_reader: Any, clock_us: Any) -> None:
        self.identity_port = identity_port
        self.attestation_reader = attestation_reader
        self.clock_us = clock_us

    def authenticate(self, supplied: Any, action: str, subject_digest: str,
                     project_id: str, affected_ref: str) -> dict[str, Any]:
        evidence = getattr(supplied, "evidence_refs", ())
        if (getattr(supplied, "allowed", None) is not True or
                getattr(supplied, "status", None) != "ALLOW" or
                getattr(supplied, "code", None) != "OK" or
                getattr(supplied, "affected_ref", None) != affected_ref or
                not isinstance(evidence, tuple) or len(evidence) != 1):
            raise BibleError("EVIDENCE_INVALID")
        proof_ref = identifier(evidence[0])
        verified = self.identity_port.verify_owner_decision(proof_ref, action, subject_digest, project_id)
        if (getattr(verified, "allowed", None) is not True or
                getattr(verified, "status", None) != "ALLOW" or
                getattr(verified, "code", None) != "OK" or
                getattr(verified, "affected_ref", None) != affected_ref or
                getattr(verified, "evidence_refs", None) != (proof_ref,)):
            raise BibleError("EVIDENCE_INVALID")
        attestation = self.attestation_reader(proof_ref)
        if not isinstance(attestation, dict):
            raise BibleError("EVIDENCE_INVALID")
        expected = {"proof_ref", "decision_id", "owner_id", "project_id", "action",
                    "subject_digest", "authority_epoch", "expires_at_us"}
        if set(attestation) != expected:
            raise BibleError("EVIDENCE_INVALID")
        for key in ("proof_ref", "decision_id", "owner_id", "project_id", "action"):
            identifier(attestation[key])
        now = self.clock_us()
        if (type(now) is not int or now < 0 or
                attestation["proof_ref"] != proof_ref or
                attestation["project_id"] != project_id or
                attestation["action"] != action or
                attestation["subject_digest"] != subject_digest or
                type(attestation["authority_epoch"]) is not int or
                attestation["authority_epoch"] < 0 or
                type(attestation["expires_at_us"]) is not int or
                attestation["expires_at_us"] <= now):
            raise BibleError("EVIDENCE_INVALID")
        return {**attestation, "now_us": now}
