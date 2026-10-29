"""Independent QA lineage comparison; identities must come from CAB-H proof.

The caller cannot convert worker-authored lineage into authenticated identity.
"""
from __future__ import annotations
from collections.abc import Mapping
from nomad_studio.verification.validation import ref, unique_refs


def reviewer_principal(identity, authors: Mapping) -> str:
    """Return authenticated reviewer or reject shared principal/root lineage.

    A successful operation result alone is insufficient: provider-authenticated
    identity and its signer/lineage must be present, and never simulated.
    """
    if getattr(identity, 'status', None) != 'OK' or not getattr(identity, 'receipt_ref', None):
        raise ValueError('unverified reviewer')
    who = getattr(identity, 'record', None)
    if not isinstance(who, Mapping) or set(who) != {
            'principal_id', 'lineage_principals', 'lineage_roots', 'issuer_id', 'authenticated', 'simulated'}:
        raise ValueError('unverified reviewer record')
    if who['authenticated'] is not True or who['simulated'] is not False:
        raise ValueError('unverified reviewer')
    principal = ref(who['principal_id'])
    ref(who['issuer_id'])
    refs = set(unique_refs(who['lineage_principals']))
    roots = set(unique_refs(who['lineage_roots']))
    if not refs or not roots or principal not in refs:
        raise ValueError('incomplete reviewer provenance')
    if not isinstance(authors, Mapping) or set(authors) != {'principals', 'roots'}:
        raise ValueError('incomplete author provenance')
    auth_principals = set(unique_refs(authors['principals']))
    auth_roots = set(unique_refs(authors['roots']))
    if not auth_principals or not auth_roots or refs & auth_principals or roots & auth_roots:
        raise ValueError('self-review or shared author lineage')
    return principal
