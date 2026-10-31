"""Byte-verified role resources and version-pinned generic capability manifests."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping
from .ports import ContentSource, HostReadiness

HEX = re.compile(r'^[0-9a-f]{64}$')
IDENTIFIER = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$')
KINDS = {'engine', 'discipline', 'genre', 'toolchain', 'template', 'tool', 'other'}
RESOURCE_KINDS = {'instructions', 'scripts', 'assets', 'validators'}

class OrganizationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require_digest(value: str) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise OrganizationError('invalid', 'expected lowercase SHA-256 digest')
    return value


def require_id(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise OrganizationError('invalid', 'invalid identifier')
    return value


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(value: Mapping[str, Any]) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False).encode('utf-8'))


def load_json(raw: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OrganizationError('invalid', 'invalid UTF-8 JSON') from exc
    if not isinstance(obj, dict):
        raise OrganizationError('invalid', 'manifest must be a JSON object')
    return obj


def checked_path(value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise OrganizationError('invalid', 'invalid resource path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('..', '.') for part in value.split('/')) or ':' in value:
        raise OrganizationError('invalid', 'resource path must be relative and confined')
    return value


def verify_blob(source: ContentSource, expected: str) -> bytes | None:
    require_digest(expected)
    raw = source.get(expected)
    if raw is not None and (not isinstance(raw, bytes) or digest(raw) != expected):
        raise OrganizationError('invalid', 'resource content SHA-256 does not match')
    return raw


@dataclass(frozen=True)
class Readiness:
    state: str
    missing: tuple[str, ...]
    manifest_digest: str
    personality: str | None = None
    reasoning_tier: str | None = None


def role_readiness(role: Mapping[str, Any], source: ContentSource,
                   host: HostReadiness | None) -> Readiness:
    """Only verified actual instructions/assets/scripts/validators yield ready."""
    manifest_digest = require_digest(role['manifest_digest'])
    raw = verify_blob(source, manifest_digest)
    if raw is None:
        return Readiness('unavailable', ('manifest:'+manifest_digest,), manifest_digest)
    body = load_json(raw)
    if body.get('role_id') != role['role_id'] or body.get('revision') != role['revision']:
        raise OrganizationError('invalid', 'role catalog and resource manifest disagree')
    missing: list[str] = []
    resources = body.get('resources')
    if not isinstance(resources, dict) or not resources.get('instructions'):
        raise OrganizationError('invalid', 'role requires instruction resource declarations')
    for kind, entries in resources.items():
        if kind not in RESOURCE_KINDS or not isinstance(entries, list):
            raise OrganizationError('invalid', 'unknown resource kind or malformed list')
        for entry in entries:
            if not isinstance(entry, dict):
                raise OrganizationError('invalid', 'resource entry must be an object')
            path = checked_path(entry.get('path'))
            resource_digest = require_digest(entry.get('sha256'))
            actual = verify_blob(source, resource_digest)
            if actual is None:
                missing.append(f'{kind}:{path}:{resource_digest}')
            elif kind in ('instructions', 'assets'):
                try:
                    if not actual.decode('utf-8').strip():
                        raise OrganizationError('invalid', 'empty role resource: '+path)
                except UnicodeDecodeError as exc:
                    raise OrganizationError('invalid', 'invalid role text resource: '+path) from exc
    tools = body.get('required_tools', [])
    if not isinstance(tools, list) or any(not isinstance(t, str) or not t for t in tools):
        raise OrganizationError('invalid', 'invalid required_tools')
    for tool in tools:
        if host is None or not host.tool_available(tool):
            missing.append('tool:'+tool)
    for validator in body.get('smoke_validators', []):
        require_digest(validator)
        if host is None or not host.validator_passed(validator):
            missing.append('smoke:'+validator)
    tier = body.get('reasoning_tier')
    if tier is not None and tier not in ('medium', 'high', 'extra_high'):
        raise OrganizationError('invalid', 'invalid reasoning tier guidance')
    personality = body.get('personality')
    if personality is not None and (not isinstance(personality, str) or len(personality)>512):
        raise OrganizationError('invalid', 'invalid personality guidance')
    return Readiness('ready' if not missing else 'unavailable',
                     tuple(missing), manifest_digest, personality, tier)


def package_manifest(raw: bytes, source: ContentSource) -> dict[str, Any]:
    """Strictly enforce frozen CONTRACTS/package.schema.json v1 plus byte checks."""
    manifest = load_json(raw)
    required = {'manifest_version','package_id','package_version','kind',
                'capabilities','dependencies','entrypoints','resources'}
    if set(manifest) - (required | {'license_id'}) or not required.issubset(manifest):
        raise OrganizationError('invalid','package manifest fields mismatch frozen v1 schema')
    if manifest['manifest_version']!='1' or manifest['kind'] not in KINDS:
        raise OrganizationError('invalid','unsupported manifest version or kind')
    for key in ('package_id','package_version'):
        if not isinstance(manifest[key],str) or not manifest[key]:
            raise OrganizationError('invalid','missing package identifier/version')
    if 'license_id' in manifest and not isinstance(manifest['license_id'],str):
        raise OrganizationError('invalid','invalid license ID')
    capabilities=manifest['capabilities']
    if (not isinstance(capabilities,list) or
        any(not isinstance(c,str) or not c for c in capabilities) or
        len(set(capabilities)) != len(capabilities)):
        raise OrganizationError('invalid','capabilities must be unique nonempty strings')
    deps = manifest['dependencies']
    if not isinstance(deps,list) or any(not isinstance(d,str) for d in deps) or len(set(deps))!=len(deps):
        raise OrganizationError('invalid','invalid or duplicate dependency')
    for dep in deps:
        require_digest(dep)
    if digest(raw) in deps:
        raise OrganizationError('invalid','self-dependent capability')
    resources=manifest['resources']
    if not isinstance(resources,list):
        raise OrganizationError('invalid','resources must be array')
    files={}
    for item in resources:
        if not isinstance(item,dict) or set(item)!={'path','sha256','byte_size'}:
            raise OrganizationError('invalid','resource fields mismatch frozen v1 schema')
        path=checked_path(item.get('path'))
        expected=require_digest(item.get('sha256'))
        size=item.get('byte_size')
        if type(size) is not int or size<0:
            raise OrganizationError('invalid','resource byte_size must be nonnegative integer')
        if path in files:
            raise OrganizationError('invalid','duplicate resource path')
        raw_file=verify_blob(source,expected)
        if raw_file is None:
            raise OrganizationError('capability_unavailable','missing package resource: '+path)
        if len(raw_file)!=size:
            raise OrganizationError('invalid','resource size mismatch: '+path)
        files[path]=expected
    entries=manifest['entrypoints']
    if not isinstance(entries,list):
        raise OrganizationError('invalid','entrypoints must be array')
    seen=set()
    fields={'id','executable_digest','args_schema_digest','output_schema_digest',
            'required_isolation','executable_path','args_schema_path','output_schema_path'}
    for ent in entries:
        if not isinstance(ent,dict) or set(ent)!=fields:
            raise OrganizationError('invalid','entrypoint fields mismatch frozen v1 schema')
        eid=require_id(ent['id'])
        if eid in seen:
            raise OrganizationError('invalid','duplicate entrypoint ID')
        seen.add(eid)
        require_id(ent['required_isolation'])
        for prefix in ('executable','args_schema','output_schema'):
            path=checked_path(ent[prefix+'_path'])
            if path not in files or files[path]!=require_digest(ent[prefix+'_digest']):
                raise OrganizationError('invalid','entrypoint file missing or wrong digest: '+path)
    return manifest


class DirectoryContentSource:
    """Read bundled content-addressed resources with confined paths and live digest checks."""
    def __init__(self, root: str):
        from pathlib import Path
        self.root = Path(root).resolve()
        index_path = self.root / 'resource-index.json'
        index = load_json(index_path.read_bytes())
        self.paths = {}
        for expected, rel in index.items():
            require_digest(expected)
            checked_path(rel)
            self.paths[expected] = rel

    def get(self, sha256_digest: str) -> bytes | None:
        from pathlib import Path
        require_digest(sha256_digest)
        rel = self.paths.get(sha256_digest)
        if rel is None:
            return None
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            return None
        raw = path.read_bytes()
        if digest(raw) != sha256_digest:
            raise OrganizationError('invalid', 'bundled source changed digest: '+rel)
        return raw
