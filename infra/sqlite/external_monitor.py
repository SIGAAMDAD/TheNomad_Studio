#!/usr/bin/env python3
"""A09 external mTLS monitor: read only, independently scheduled, no publish right."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone,timedelta
import json
import ssl
from urllib.parse import urlsplit
from urllib.request import Request,urlopen

class Degraded(Exception):
    pass

def _date(value):
    if not isinstance(value,str) or not value.endswith('Z'):
        raise Degraded('TIMESTAMP_INVALID')
    try:
        return datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:
        raise Degraded('TIMESTAMP_INVALID') from None

def evaluate(sample,*,expected_mode='SHADOW',now=None,freshness_seconds=30):
    if expected_mode not in ('SHADOW','ACTIVE') or not 1<=freshness_seconds<=120:
        raise ValueError('invalid monitor policy')
    if not isinstance(sample,dict) or sample.get('schema')!='NOMAD-SERVICE-HEALTH/2':
        raise Degraded('SCHEMA_MISMATCH')
    if sample.get('mode')!=expected_mode:
        raise Degraded('AUTHORITY_MODE_MISMATCH')
    if sample.get('database_integrity')!='ok':
        raise Degraded('DATABASE_INTEGRITY_DEGRADED')
    if not isinstance(sample.get('schema_migration_count'),int) or sample['schema_migration_count']<1:
        raise Degraded('SCHEMA_MIGRATIONS_MISSING')
    if expected_mode=='SHADOW' and sample.get('external_effects_enabled') is not False:
        raise Degraded('SHADOW_PUBLICATION_UNSAFE')
    if sample.get('journal_mode')!='wal' or sample.get('synchronous')!=2:
        raise Degraded('DURABILITY_CONFIGURATION_DEGRADED')
    now=now or datetime.now(timezone.utc)
    stamp=_date(sample.get('observed_at'))
    if stamp>now+timedelta(seconds=2) or now-stamp>timedelta(seconds=freshness_seconds):
        raise Degraded('HEALTH_SAMPLE_STALE')
    return {'schema':'NOMAD-EXTERNAL-MONITOR/1','status':'HEALTHY',
            'observed_at':sample['observed_at'],'mode':expected_mode,
            'external_evidence':'UNREVIEWED_MONITOR_CAPTURE'}

def fetch(url,*,cafile,certfile,keyfile,timeout_seconds=5):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path!='/v2/health':
        raise ValueError('TLS authenticated fixed health URL required')
    if not parsed.hostname or not 1<=timeout_seconds<=15:
        raise ValueError('invalid monitoring host')
    tls=ssl.create_default_context(ssl.Purpose.SERVER_AUTH,cafile=cafile)
    tls.minimum_version=ssl.TLSVersion.TLSv1_2
    tls.verify_mode=ssl.CERT_REQUIRED
    tls.check_hostname=True
    tls.load_cert_chain(certfile,keyfile)
    req=Request(url,method='GET',headers={'Accept':'application/json'})
    with urlopen(req,context=tls,timeout=timeout_seconds) as resp:
        if resp.status!=200:
            raise Degraded('HEALTH_ENDPOINT_UNAVAILABLE')
        raw=resp.read(16385)
        if len(raw)>16384:
            raise Degraded('OVERSIZE_HEALTH_SAMPLE')
    return json.loads(raw)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('url','ca','cert','key'):
        ap.add_argument('--'+name,required=True)
    ap.add_argument('--expect-mode',choices=('SHADOW','ACTIVE'),default='SHADOW')
    args=ap.parse_args()
    try:
        result=evaluate(fetch(args.url,cafile=args.ca,certfile=args.cert,keyfile=args.key),
                        expected_mode=args.expect_mode)
    except Exception as exc:
        result={'schema':'NOMAD-EXTERNAL-MONITOR/1','status':'DEGRADED',
                'code':exc.args[0] if isinstance(exc,Degraded) else 'UNAVAILABLE_OR_INVALID',
                'external_evidence':'UNREVIEWED_MONITOR_CAPTURE'}
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='HEALTHY' else 2

if __name__=='__main__':
    raise SystemExit(main())
