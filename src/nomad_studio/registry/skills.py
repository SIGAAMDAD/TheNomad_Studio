"""Verified installed skill and project-adapter discovery.

Trusted readers are injected by P2. These methods never treat a skill name,
prompt, project roster or an agent-reported digest as proof of installation.
"""
from __future__ import annotations

from hashlib import sha256
import re
from collections.abc import Callable, Mapping
from typing import Any

from .catalog import Specialization

_HASH = re.compile(r'^[a-f0-9]{64}$')
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')

class SkillVerificationError(ValueError):
    pass


def verify_installed_skill(skill: Specialization, pin: Mapping[str,Any],
                           installed_reader: Callable[[str], Mapping[str, Any]]) -> dict:
    if not isinstance(pin, Mapping) or set(pin) - {'uri','version','sha256','archive_sha256','requirements'}:
        raise SkillVerificationError('invalid pinned skill manifest')
    if (pin.get('uri') != skill.skill_uri or pin.get('version') != skill.skill_version or
        not isinstance(pin.get('sha256'),str) or not _HASH.fullmatch(pin['sha256'])):
        raise SkillVerificationError('unverified skill pin')
    req = pin.get('requirements',())
    if not isinstance(req,(list,tuple)) or any(not isinstance(r,str) or not _ID.fullmatch(r) for r in req):
        raise SkillVerificationError('invalid skill requirements')
    try:
        installed = installed_reader(skill.skill_uri)
    except Exception as exc:
        raise SkillVerificationError('installed skill readback unavailable') from exc
    if not isinstance(installed,Mapping) or installed.get('uri') != skill.skill_uri or installed.get('version') != skill.skill_version:
        raise SkillVerificationError('wrong installed skill version')
    body = installed.get('content')
    if isinstance(body,str):
        body = body.encode('utf-8')
    if not isinstance(body,bytes) or not body or sha256(body).hexdigest() != pin['sha256']:
        raise SkillVerificationError('installed bytes do not match pinned digest')
    if (not isinstance(installed.get('evidence_ref'),str) or not _ID.fullmatch(installed['evidence_ref'])
        or installed.get('observed') is not True):
        raise SkillVerificationError('no trusted installed skill observation')
    if skill.source_archive_sha256 is not None:
        if (pin.get('archive_sha256') != skill.source_archive_sha256 or
            installed.get('archive_sha256') != skill.source_archive_sha256):
            raise SkillVerificationError('archive source digest mismatch')
    return {'skill_id':skill.id, 'uri':skill.skill_uri, 'version':skill.skill_version,
            'source_digest':pin['sha256'], 'requirements':list(req),
            'evidence_ref':installed['evidence_ref'], 'project_adapter':False}


def verify_adapter_manifest(manifest: Mapping[str, Any]) -> dict:
    """Treat Godot/asset/audio capabilities as arbitrary named adapter data."""
    if (not isinstance(manifest,Mapping) or set(manifest) !=
        {'adapter_id','source_digest','capabilities','evidence_ref','observed'} or
        not isinstance(manifest['adapter_id'],str) or not _ID.fullmatch(manifest['adapter_id']) or
        not isinstance(manifest['source_digest'],str) or not _HASH.fullmatch(manifest['source_digest']) or
        not isinstance(manifest['evidence_ref'],str) or not _ID.fullmatch(manifest['evidence_ref']) or
        manifest['observed'] is not True):
        raise SkillVerificationError('unverified adapter manifest')
    caps = manifest['capabilities']
    if (not isinstance(caps,list) or not caps or len(caps) > 512 or
        any(not isinstance(v,str) or not _ID.fullmatch(v) for v in caps) or len(set(caps)) != len(caps)):
        raise SkillVerificationError('invalid adapter capabilities')
    return {'adapter_id':manifest['adapter_id'], 'source_digest':manifest['source_digest'],
            'capabilities':tuple(caps), 'evidence_ref':manifest['evidence_ref']}
