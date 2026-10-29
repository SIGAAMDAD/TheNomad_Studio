"""Private Oversight -> Studio facade; no HR case data or implicit authority."""
from __future__ import annotations
from typing import Any, Mapping
from nomad_studio.trust.port_types import Result, refused
from nomad_studio.oversight.codec import CLAIM_FIELDS

class PrivateOversightBridge:
    def __init__(self, notice_port: Any):
        self.notice_port = notice_port

    def submit(self, signed_claims: Mapping[str, Any], signature: Mapping[str, Any]) -> Result:
        if not isinstance(signed_claims,Mapping) or set(signed_claims) != CLAIM_FIELDS:
            return refused('INVALID_INPUT')
        if not isinstance(signature,Mapping) or set(signature) != {'key_id','signature','proof_ref'}:
            return refused('INVALID_INPUT')
        return self.notice_port.ingest_signed_notice(dict(signed_claims),dict(signature))
