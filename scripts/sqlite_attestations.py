#!/usr/bin/env python3
"""Ed25519 detached attestations. Trust and revocation data MUST be off checkout."""
from __future__ import annotations
import base64
import binascii
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
import re
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

SHA = re.compile(r'^[0-9a-f]{40}$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
ID = re.compile(r'^[A-Za-z0-9_.:-]{3,128}$')
KINDS = ('INDEPENDENT_QA','OWNER_STAGING_MERGE','EXTERNAL_PROVIDER',
         'OFFHOST_RESTORE','HR_FAULT_MATRIX','PERFORMANCE',
         'OWNER_AUTHORITY_CUTOVER','OWNER_PRODUCTION_RELEASE')

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')

def evidence_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def _instant(value):
    if not isinstance(value,str) or not value.endswith('Z'):
        raise ValueError('invalid attestation timestamp')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset()!=timedelta(0):
        raise ValueError('timestamp must be UTC')
    return result

def load_external_trust(environ=None):
    env=os.environ if environ is None else environ
    principals={}
    for kind in KINDS:
        prefix='NOMAD_'+kind
        pem,identity=env.get(prefix+'_PUBLIC_KEY_PEM'),env.get(prefix+'_ISSUER_ID')
        if pem is not None or identity is not None:
            if not pem or not identity or not ID.fullmatch(identity):
                raise ValueError('incomplete signer configuration: '+kind)
            key=load_pem_public_key(pem.encode('ascii'))
            if not isinstance(key,Ed25519PublicKey):
                raise ValueError('non-Ed25519 signing key')
            principals[kind]=(identity,key)
    implementer=env.get('NOMAD_IMPLEMENTER_ID')
    if not implementer or not ID.fullmatch(implementer):
        raise ValueError('protected implementer identity unavailable')
    revoked=env.get('NOMAD_REVOKED_ATTESTATION_NONCES_JSON')
    if revoked is None:
        raise ValueError('protected revocation snapshot unavailable')
    revocations=json.loads(revoked)
    if not isinstance(revocations,list) or not all(isinstance(x,str) and ID.fullmatch(x) for x in revocations):
        raise ValueError('invalid protected revocation snapshot')
    return dict(principals=principals,implementer=implementer,revoked=set(revocations))

def verify_one(envelope,kind,*,trust,head_sha,checkout_sha,inventory_sha,receipt_digest,now=None):
    try:
        if kind not in KINDS or kind not in trust['principals']:
            raise ValueError('unknown independent issuer')
        payload=envelope['payload']
        if set(envelope)!={'payload','signature'}:
            raise ValueError('unexpected envelope fields')
        allowed={'schema','kind','issuer_id','subject_sha','checkout_sha','inventory_sha',
                 'evidence_sha256','implementer_id','policy_version','nonce','issued_at','expires_at'}
        if set(payload)!=allowed or payload['schema']!='NOMAD-SIGNED-ATTESTATION/1' or payload['kind']!=kind:
            raise ValueError('invalid signed schema')
        issuer_id,key=trust['principals'][kind]
        if payload['issuer_id']!=issuer_id or payload['issuer_id']==trust['implementer']:
            raise ValueError('nonindependent approval')
        if payload['implementer_id']!=trust['implementer']:
            raise ValueError('implementer identity mismatch')
        if not all(SHA.fullmatch(v) for v in (head_sha,checkout_sha,inventory_sha)):
            raise ValueError('source SHA invalid')
        if not SHA256.fullmatch(receipt_digest):
            raise ValueError('digest invalid')
        if (payload['subject_sha']!=head_sha or payload['checkout_sha']!=checkout_sha or
            payload['inventory_sha']!=inventory_sha or payload['evidence_sha256']!=receipt_digest):
            raise ValueError('attestation subject mismatch')
        if not ID.fullmatch(payload['nonce']) or payload['nonce'] in trust['revoked']:
            raise ValueError('revoked nonce')
        if not ID.fullmatch(payload['policy_version']):
            raise ValueError('policy invalid')
        issued,expires=_instant(payload['issued_at']),_instant(payload['expires_at'])
        now=now or datetime.now(timezone.utc)
        if issued>now+timedelta(minutes=2) or now>=expires or expires<=issued or expires-issued>timedelta(days=7):
            raise ValueError('stale attestation')
        sig=base64.b64decode(envelope['signature'],validate=True)
        if len(sig)!=64:
            raise ValueError('signature length')
        key.verify(sig,canonical(payload))
    except (TypeError,KeyError,AttributeError,ValueError,binascii.Error,InvalidSignature) as ex:
        raise ValueError('invalid independently signed proof: '+type(ex).__name__) from None
    return payload

def verify_set(attestations,kinds,*,trust,head_sha,checkout_sha,inventory_sha,receipt_digest,now=None):
    if not isinstance(attestations,dict) or attestations.get('schema')!='NOMAD-ATTESTATION-BUNDLE/1':
        raise ValueError('external attestation bundle required')
    entries=attestations.get('attestations')
    if not isinstance(entries,dict):
        raise ValueError('invalid bundle')
    verified={}
    for kind in kinds:
        verified[kind]=verify_one(entries[kind],kind,trust=trust,head_sha=head_sha,
                                  checkout_sha=checkout_sha,inventory_sha=inventory_sha,
                                  receipt_digest=receipt_digest,now=now)
    nonces=[v['nonce'] for v in verified.values()]
    if len(nonces)!=len(set(nonces)):
        raise ValueError('duplicate approval nonces')
    actors=[verified[k]['issuer_id'] for k in ('INDEPENDENT_QA','OWNER_STAGING_MERGE') if k in verified]
    if len(actors)!=len(set(actors)):
        raise ValueError('reviewer and owner identity must differ')
    return verified
