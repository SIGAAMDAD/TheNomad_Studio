"""Strict, closed artifact manifests. No metadata here is a byte/QA receipt."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

KINDS = frozenset({'mesh', 'audio', 'fmod_bank', 'fmod_project', 'animation', 'image', 'vfx', 'ui', 'build', 'document'})
EXTENSIONS = {
    'mesh': {'.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json', '.obj': 'model/obj', '.fbx': 'application/octet-stream'},
    'audio': {'.wav': 'audio/wav', '.ogg': 'audio/ogg', '.flac': 'audio/flac'},
    'fmod_bank': {'.bank': 'application/x-fmod-bank'},
    'fmod_project': {'.fspro': 'application/x-fmod-project'},
    'animation': {'.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json', '.fbx': 'application/octet-stream'},
    'image': {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.exr': 'image/x-exr'},
    'vfx': {'.glb': 'model/gltf-binary', '.png': 'image/png', '.tscn': 'text/plain', '.tres': 'text/plain'},
    'ui': {'.png': 'image/png', '.svg': 'image/svg+xml', '.tscn': 'text/plain'},
    'build': {'.zip': 'application/zip', '.pck': 'application/x-godot-pck'},
    'document': {'.md': 'text/markdown', '.txt': 'text/plain', '.json': 'application/json', '.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
}
DOMAINS = frozenset({'design', 'narrative', 'gameplay', 'environment', 'audio', 'fmod', 'art', 'animation', 'ui', 'vfx', 'qa', 'production', 'engineering', 'publishing'})
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
HEX = re.compile(r'^[0-9a-f]{64}$')
FIELDS = frozenset({'artifact_id', 'kind', 'provider', 'object_id', 'revision', 'parent_id', 'filename', 'size', 'sha256', 'mime', 'license', 'origin', 'document_domain', 'fmod_contract'})


class SchemaError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def fail(message: str, code: str = 'INVALID_SCHEMA') -> None:
    raise SchemaError(code, message)


def _text(value: object, name: str, max_length: int = 512) -> str:
    if not isinstance(value, str) or not (0 < len(value) <= max_length) or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        fail(f'{name} invalid')
    return value


def _closed(value: object, keys: frozenset[str], required: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - keys:
        fail('object fields missing/unknown')
    return value


def _fmod_contract(raw: object) -> dict[str, Any]:
    c = _closed(raw, frozenset({'bank_name', 'project_name', 'events', 'parameters', 'audio_review_required'}), frozenset({'events', 'parameters', 'audio_review_required'}))
    if c['audio_review_required'] is not True:
        fail('FMOD artifact requires independent audio review')
    if not ('bank_name' in c or 'project_name' in c):
        fail('FMOD bank_name or project_name required')
    for k in ('bank_name', 'project_name'):
        if k in c:
            _text(c[k], k, 160)
    events = c['events']
    if not isinstance(events, list) or not events or len(events) > 4096:
        fail('FMOD events must be nonempty bounded list')
    paths: set[str] = set()
    for e in events:
        v = _closed(e, frozenset({'path', 'guid'}), frozenset({'path'}))
        path = _text(v['path'], 'event path', 256)
        if not path.startswith('event:/') or path == 'event:/' or path in paths:
            fail('invalid or duplicate event path')
        paths.add(path)
        if 'guid' in v:
            _text(v['guid'], 'event GUID', 64)
    params = c['parameters']
    if not isinstance(params, list) or len(params) > 4096:
        fail('invalid FMOD parameters')
    names: set[str] = set()
    for p in params:
        v = _closed(p, frozenset({'name', 'type', 'minimum', 'maximum', 'default', 'labels'}), frozenset({'name', 'type'}))
        name = _text(v['name'], 'parameter name', 160)
        if name in names or v['type'] not in ('continuous', 'discrete', 'labeled'):
            fail('duplicate or invalid parameter')
        names.add(name)
        if v['type'] in ('continuous', 'discrete'):
            if not all(k in v for k in ('minimum', 'maximum', 'default')):
                fail('parameter range/default missing')
            values = [v[k] for k in ('minimum', 'maximum', 'default')]
            if any(type(x) not in (int, float) or not (-1e12 < x < 1e12) for x in values) or not values[0] <= values[2] <= values[1]:
                fail('parameter bounds invalid')
            if v['type'] == 'discrete' and any(type(x) is not int for x in values):
                fail('discrete parameter must be integer')
            if 'labels' in v:
                fail('numeric parameter has labels')
        else:
            labels = v.get('labels')
            if any(k in v for k in ('minimum', 'maximum')) or not isinstance(labels, list) or not labels or len(labels) > 1000 or any(not isinstance(x, str) or not x.strip() for x in labels) or len(set(labels)) != len(labels) or type(v.get('default')) is not int or not (0 <= v['default'] < len(labels)):
                fail('labeled parameter invalid')
    return dict(c)


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    artifact_id: str
    kind: str
    provider: str
    object_id: str
    revision: str
    parent_id: str | None
    filename: str
    size: int
    sha256: str
    mime: str
    license: str
    origin: str
    document_domain: str | None = None
    fmod_contract: Mapping[str, Any] | None = None

    @classmethod
    def parse(cls, raw: object, folders: Mapping[str, str]) -> 'ArtifactManifest':
        required = FIELDS - {'parent_id', 'document_domain', 'fmod_contract'}
        r = _closed(raw, FIELDS, required)
        artifact_id = _text(r['artifact_id'], 'artifact_id', 128)
        if not ID.fullmatch(artifact_id):
            fail('artifact_id contains disallowed characters')
        kind = r['kind']
        provider = r['provider']
        if kind not in KINDS or provider not in ('drive', 'github', 'lfs'):
            fail('kind/provider not supported')
        filename = _text(r['filename'], 'filename', 240)
        if filename in ('.', '..') or '/' in filename or '\\' in filename or filename.startswith('.') or filename[-1] in ('.', ' '):
            fail('unsafe filename', 'INVALID_PATH')
        extension = '.' + filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if EXTENSIONS[kind].get(extension) != r['mime']:
            fail('filename extension/kind/MIME conflict')
        sha = r['sha256']
        if not isinstance(sha, str) or not HEX.fullmatch(sha):
            fail('invalid sha256')
        size = r['size']
        if type(size) is not int or size <= 0:
            fail('size must be positive integer')
        object_id = _text(r['object_id'], 'object_id', 512)
        if provider == 'github':
            if any(p in ('.', '..', '') for p in object_id.split('/')) or object_id.startswith('/') or '\\' in object_id:
                fail('unsafe GitHub path', 'INVALID_PATH')
        else:
            if '/' in object_id or '\\' in object_id or object_id in ('.', '..'):
                fail('unsafe provider object ID', 'INVALID_PATH')
        revision = _text(r['revision'], 'revision', 200)
        parent = r.get('parent_id')
        if provider == 'drive':
            parent = _text(parent, 'parent_id', 200)
            folder_key = f'document:{r.get("document_domain")}' if kind == 'document' else f'asset:{kind}'
            expected = folders.get(folder_key)
            if not expected:
                fail(f'folder registry missing {folder_key}', 'NOT_CONFIGURED')
            if parent != expected:
                fail('Drive parent is not the registered dedicated folder', 'INVALID_SCOPE')
        elif parent is not None:
            fail('non-Drive parent_id must be omitted')
        domain = r.get('document_domain')
        if kind == 'document':
            if domain not in DOMAINS or provider != 'drive':
                fail('document requires domain and Drive provider')
        elif domain is not None:
            fail('document_domain only allowed for document')
        fmod = r.get('fmod_contract')
        if kind in ('fmod_bank', 'fmod_project'):
            fmod = _fmod_contract(fmod)
            if kind == 'fmod_bank' and 'bank_name' not in fmod:
                fail('bank requires bank_name')
            if kind == 'fmod_project' and 'project_name' not in fmod:
                fail('project requires project_name')
        elif fmod is not None:
            fail('fmod_contract only allowed for FMOD artifact')
        return cls(artifact_id, kind, provider, object_id, revision, parent, filename, size, sha, r['mime'], _text(r['license'], 'license', 256), _text(r['origin'], 'origin', 256), domain, fmod)

    def dict(self) -> dict[str, Any]:
        result = {k: getattr(self, k) for k in ('artifact_id', 'kind', 'provider', 'object_id', 'revision', 'parent_id', 'filename', 'size', 'sha256', 'mime', 'license', 'origin')}
        if self.document_domain is not None:
            result['document_domain'] = self.document_domain
        if self.fmod_contract is not None:
            result['fmod_contract'] = self.fmod_contract
        if self.parent_id is None:
            del result['parent_id']
        return result

    def digest(self) -> str:
        return sha256_json(self.dict())


def sha256_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
