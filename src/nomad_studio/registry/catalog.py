"""Project-independent installed-specialization source roster, without rank authority.

The historical 174 ids are retained as provenance-pinned project data. No
number of specializations, sessions, departments or manager roles is imposed
on the general Studio service. Project catalog instances may be entirely custom.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
import json
import re
from types import MappingProxyType
from typing import Mapping

BASE_FILE_SHA256 = '905e84bad5c6fb19133702fc5c6d53b8f497667e0ddf356624f1d5215ad263d9'
FMOD_FILE_SHA256 = 'a396ffe2e4511106b59f9933151b0a25c0e4c14db1079539416f0c8376da2607'
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
_URI = re.compile(r'^skills://[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$')

@dataclass(frozen=True, slots=True)
class Specialization:
    id: str
    title: str
    skill_uri: str
    skill_version: str
    provenance: str
    source_archive_sha256: str | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v for v in (self.title,self.skill_version,self.provenance)):
            raise ValueError('invalid specialization')
        if not _ID.fullmatch(self.id) or not _URI.fullmatch(self.skill_uri):
            raise ValueError('invalid specialization id / uri')
        if (self.source_archive_sha256 is not None and
            not re.fullmatch('[0-9a-f]{64}', self.source_archive_sha256)):
            raise ValueError('invalid archive digest')

@dataclass(frozen=True, slots=True)
class Catalog:
    project_id: str
    specializations: Mapping[str, Specialization]
    source_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.project_id):
            raise ValueError('invalid project')
        if any(k != v.id for k,v in self.specializations.items()):
            raise ValueError('specialization key mismatch')
        object.__setattr__(self, 'specializations', MappingProxyType(dict(self.specializations)))

    def assign_to_cabal(self, specialization_ids: list[str], cabal_id: str) -> dict:
        """Cabal placement is metadata, not a permission or rank grant."""
        if not _ID.fullmatch(cabal_id) or len(set(specialization_ids)) != len(specialization_ids):
            raise ValueError('invalid cabal assignment')
        if any(s not in self.specializations for s in specialization_ids):
            raise ValueError('unregistered specialization')
        return {'project_id':self.project_id, 'cabal_id':cabal_id,
                'specialization_ids':tuple(specialization_ids)}


def _bundled(filename: str, expected_digest: str) -> dict:
    data = resources.files('nomad_studio.registry').joinpath('data',filename).read_bytes()
    if sha256(data).hexdigest() != expected_digest:
        raise ValueError('bundled roster source digest mismatch')
    return json.loads(data)


def project_catalog(project_id: str, specializations: list[Specialization]) -> Catalog:
    by_id = {entry.id:entry for entry in specializations}
    if len(by_id) != len(specializations):
        raise ValueError('duplicate specialization')
    return Catalog(project_id,by_id)


def load_source_roster(project_id: str, *, include_fmod: bool = False) -> Catalog:
    """Explicitly import a pinned project dataset; never bootstrap it globally."""
    base = _bundled('base_roster.json',BASE_FILE_SHA256)
    if base.get('schema') != 'NOMAD/ROLE-CATALOG/1' or not isinstance(base.get('roles'),list):
        raise ValueError('roster source invalid')
    entries = [Specialization(r['id'],r['title'],r['skill_uri'],r['skill_version'],r['provenance'])
               for r in base['roles']]
    source_digests = [BASE_FILE_SHA256]
    if include_fmod:
        family = _bundled('fmod_overlay.json',FMOD_FILE_SHA256)
        if not isinstance(family.get('roles'),list):
            raise ValueError('overlay source invalid')
        entries.extend(Specialization(r['name'],r['title'],f"skills://{r['name']}",
            'fmod-family-manifest-v1','FMOD family pinned source',r['sha256'])
            for r in family['roles'])
        source_digests.append(FMOD_FILE_SHA256)
    catalog = project_catalog(project_id,entries)
    return Catalog(catalog.project_id,catalog.specializations,tuple(source_digests))
