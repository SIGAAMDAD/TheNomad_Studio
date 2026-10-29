"""Optional R9 persona style data, never an authentication / QC capability.

P2 owns trusted worker identity, permission checks and skill qualification.
This service stores only pinned source digest in v1_persona_refs, and returns
plain style text after byte-level and shape verification.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any

from nomad_studio.registry.results import Result

_ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
_HASH=re.compile(r'^[0-9a-f]{64}$')
_ALLOWED=frozenset({'tone','verbosity','format_preference','style_notes'})
_FORBIDDEN=frozenset({'permissions','scopes','grants','role','rank','department','authority',
                      'access','qa_override','review_status','bible_override','owner','tool_access'})


def _valid_style(data: Any) -> bool:
    if not isinstance(data,Mapping) or not data or set(data)-_ALLOWED:
        return False
    if any(k.lower() in _FORBIDDEN for k in data):
        return False
    if any(not isinstance(v,str) or not v.strip() or len(v)>500 or '\x00' in v or
           any(z in v.lower() for z in ('ignore previous instructions','grant me permission',
                                       'override qa','owner approval','bypass authorization'))
           for v in data.values()):
        return False
    return True


class PersonaService:
    def __init__(self, deps: Mapping[str, Any]):
        self.reader=deps['reader']
        self.writer=deps['writer']
        self.style_reader=deps.get('style_reader')
        self.authorize_style_update=deps.get('authorize_style_update')

    def set_style_ref(self, worker_id: str, persona_source_digest: str, owner_proof_ref: str) -> Result:
        if (not isinstance(worker_id,str) or not _ID.fullmatch(worker_id) or
            not isinstance(persona_source_digest,str) or not _HASH.fullmatch(persona_source_digest) or
            not isinstance(owner_proof_ref,str) or not _ID.fullmatch(owner_proof_ref)):
            return Result('REJECTED','INVALID_INPUT')
        if self.style_reader is None or self.authorize_style_update is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            if self.authorize_style_update(worker_id,persona_source_digest,owner_proof_ref) is not True:
                return Result('REJECTED','PERMISSION_DENIED')
            observed=self.style_reader(persona_source_digest)
            if (not isinstance(observed,Mapping) or observed.get('observed') is not True or
                not isinstance(observed.get('content'),bytes) or
                sha256(observed['content']).hexdigest()!=persona_source_digest or
                not isinstance(observed.get('evidence_ref'),str) or not _ID.fullmatch(observed['evidence_ref'])):
                return Result('NOT_VERIFIED','EVIDENCE_INVALID')
            content=json.loads(observed['content'])
            if not _valid_style(content):
                return Result('REJECTED','PERMISSION_DENIED')
        except (ValueError,TypeError,UnicodeError,KeyError):
            return Result('REJECTED','INVALID_INPUT')
        except Exception:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            self.writer.write(lambda conn: conn.execute(
                'INSERT INTO v1_persona_refs(worker_id,persona_source_digest,style_only) VALUES(?,?,1) '
                'ON CONFLICT(worker_id) DO UPDATE SET persona_source_digest=excluded.persona_source_digest,style_only=1',
                (worker_id,persona_source_digest)))
        except Exception:
            return Result('BLOCKED_LOCAL','LOCAL_RECOVERY_REQUIRED')
        return Result('OK','OK',{'worker_id':worker_id,'style_only':True},
                      evidence_refs=(observed['evidence_ref'],))

    def get_style(self, worker_id: str) -> Result:
        if not isinstance(worker_id,str) or not _ID.fullmatch(worker_id):
            return Result('REJECTED','INVALID_INPUT')
        if self.style_reader is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            row=self.reader.read(lambda conn:conn.execute(
                'SELECT persona_source_digest,style_only FROM v1_persona_refs WHERE worker_id=?',
                (worker_id,)).fetchone())
            if row is None:
                return Result('NOT_FOUND','NOT_FOUND')
            if row['style_only'] != 1:
                return Result('REJECTED','PERMISSION_DENIED')
            digest=row['persona_source_digest']
            evidence=self.style_reader(digest)
            if (evidence.get('observed') is not True or not isinstance(evidence.get('content'),bytes) or
                sha256(evidence['content']).hexdigest()!=digest or
                not _ID.fullmatch(evidence.get('evidence_ref'))):
                return Result('NOT_VERIFIED','EVIDENCE_INVALID')
            style=json.loads(evidence['content'])
            if not _valid_style(style):
                return Result('REJECTED','PERMISSION_DENIED')
        except Exception:
            return Result('NOT_VERIFIED','EVIDENCE_INVALID')
        return Result('OK','OK',{'worker_id':worker_id,'style':dict(style),'style_only':True},
                      evidence_refs=(evidence['evidence_ref'],))


def build_persona_service(deps: Mapping[str, Any]) -> PersonaService:
    return PersonaService(deps)
