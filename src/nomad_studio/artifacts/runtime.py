"""R9 ArtifactPort: real bytes, exact task/feature/epoch lineage, immutable CAS."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from nomad_studio.production.context.core import (
    Result, Invalid, check_id, check_sha, check_lineage, digest, failure,
    normalize_path, row_one, run_record, verified_bytes,
)

_REQUIRED = {'artifact_id', 'project_id', 'feature_id', 'task_id', 'kind', 'resource_path',
             'content_sha256', 'producer_principal', 'bible_pair_digest',
             'feature_contract_digest', 'byte_proof_ref'}
_KINDS = {'SOURCE', 'ASSET', 'SCENE', 'QUEST_GRAPH', 'DIALOGUE', 'AUDIO', 'TEST_LOG', 'OTHER'}


class ArtifactService:
    def __init__(self, db: Any, cas: Any, identity: Any, clock_us: Any):
        self.db, self.cas, self.identity, self.clock_us = db, cas, identity, clock_us

    def submit_artifacts(self, task_id: str, lease_epoch: int, manifests: Sequence[Mapping],
                         source_proofs: Mapping) -> Result:
        try:
            check_id(task_id)
            if type(lease_epoch) is not int or lease_epoch <= 0 or not manifests or len(manifests) > 1024:
                raise Invalid('INVALID_INPUT', 'invalid task, lease epoch or manifest batch')
            task = row_one(self.db, 'SELECT * FROM v1_tasks WHERE task_id=?', (task_id,))
            if task is None:
                raise Invalid('NOT_FOUND', 'task does not exist')
            run = run_record(self.db, task['feature_id'])
            lease = row_one(self.db, 'SELECT * FROM v1_worker_leases WHERE task_id=? AND epoch=? AND fenced=0',
                            (task_id, lease_epoch))
            if lease is None or lease['expires_at_us'] <= int(self.clock_us()):
                raise Invalid('LEASE_FENCED', 'worker lease no longer active')
            if task['graph_revision'] != run['graph_revision']:
                raise Invalid('REVISION_CONFLICT', 'task graph is stale')
            if (not isinstance(source_proofs, Mapping) or
                set(source_proofs) != {'project_id', 'feature_id', 'bible_pair_digest',
                                       'feature_contract_digest', 'graph_revision', 'producer_principal', 'proof_ref'}):
                raise Invalid('INVALID_INPUT', 'source proof scope is incomplete')
            check_lineage(run, source_proofs)
            if source_proofs['graph_revision'] != run['graph_revision']:
                raise Invalid('REVISION_CONFLICT', 'wrong graph revision')
            if not self.identity.verify_task_author(lease, source_proofs['producer_principal'], source_proofs['proof_ref']):
                raise Invalid('EVIDENCE_INVALID', 'task author proof not verified')
            checked = []
            seen = set()
            for manifest in manifests:
                if (not isinstance(manifest, Mapping) or not _REQUIRED <= set(manifest)
                        or set(manifest) - _REQUIRED - {'source_parent_commit'}):
                    raise Invalid('INVALID_INPUT', 'artifact manifest schema error')
                if manifest['kind'] not in _KINDS:
                    raise Invalid('INVALID_INPUT', 'invalid artifact kind')
                artifact_id = check_id(manifest['artifact_id'])
                if artifact_id in seen or manifest['task_id'] != task_id:
                    raise Invalid('WRONG_FEATURE', 'duplicate or unrelated artifact')
                seen.add(artifact_id)
                check_lineage(run, manifest)
                if manifest['producer_principal'] != source_proofs['producer_principal']:
                    raise Invalid('EVIDENCE_INVALID', 'author mismatch')
                if manifest['kind'] in ('SOURCE', 'SCENE'):
                    check_sha(manifest.get('source_parent_commit'), commit=True)
                elif 'source_parent_commit' in manifest:
                    check_sha(manifest['source_parent_commit'], commit=True)
                path, _ = normalize_path(manifest['resource_path'])
                if manifest['kind'] in ('SOURCE', 'SCENE') and path.startswith('.git/'):
                    raise Invalid('SCOPE_EXCEEDED', 'git internals are not artifacts')
                content_hash = check_sha(manifest['content_sha256'])
                data = verified_bytes(self.cas, manifest['byte_proof_ref'], content_hash)
                checked.append((artifact_id, dict(manifest), len(data)))
            def insert(tx: Any) -> None:
                # Recheck authoritative worker epoch in same writer transaction.
                current = tx.execute('SELECT * FROM v1_worker_leases WHERE lease_id=?',
                                     (lease['lease_id'],)).fetchone()
                if current is None or current['fenced'] or current['epoch'] != lease_epoch or current['expires_at_us'] <= int(self.clock_us()):
                    raise Invalid('LEASE_FENCED', 'worker lease invalidated during byte verification')
                for art_id, manifest, size in checked:
                    encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':'))
                    old = tx.execute('SELECT * FROM v1_artifact_manifests WHERE artifact_id=?', (art_id,)).fetchone()
                    if old is not None:
                        if old['payload_json'] != encoded:
                            raise Invalid('IDEMPOTENCY_CONFLICT', 'artifact id reused with changed contents')
                        continue
                    tx.execute('INSERT INTO v1_artifact_manifests '
                               '(feature_id,artifact_id,task_id,kind,path,content_digest,byte_proof_ref,payload_json) '
                               'VALUES (?,?,?,?,?,?,?,?)',
                               (run['feature_id'], art_id, task_id, manifest['kind'],
                                manifest['resource_path'], manifest['content_sha256'], manifest['byte_proof_ref'], encoded))
                    if manifest['kind'] in ('ASSET', 'AUDIO', 'SCENE'):
                        tx.execute('INSERT OR IGNORE INTO v1_asset_cas '
                                   '(content_digest,storage_ref,size_bytes,media_type) VALUES (?,?,?,?)',
                                   (manifest['content_sha256'], manifest['byte_proof_ref'], size, manifest['kind']))
            self.db.write(insert)
            return Result('OK', 'OK', {'artifact_ids': [x[0] for x in checked],
                                      'feature_id': run['feature_id']},
                          'artifacts-' + digest([x[1] for x in checked]),
                          tuple(x[1]['byte_proof_ref'] for x in checked))
        except Invalid as exc:
            return failure(exc)

    def verify_artifact_bytes(self, manifest_ref: str, expected_digest: str) -> Result:
        try:
            check_id(manifest_ref)
            check_sha(expected_digest)
            row = row_one(self.db, 'SELECT * FROM v1_artifact_manifests WHERE artifact_id=?',
                          (manifest_ref,))
            if row is None:
                raise Invalid('NOT_FOUND', 'manifest not found')
            if row['content_digest'] != expected_digest:
                raise Invalid('EVIDENCE_INVALID', 'requested digest differs from stored manifest')
            data = verified_bytes(self.cas, row['byte_proof_ref'], expected_digest)
            return Result('OK', 'OK', {'artifact_id': manifest_ref, 'content_sha256': expected_digest,
                                      'size_bytes': len(data)}, manifest_ref, (row['byte_proof_ref'],))
        except Invalid as exc:
            return failure(exc)

    def get_feature_manifest(self, feature_id: str, contract_digest: str) -> Result:
        try:
            run = run_record(self.db, feature_id)
            if run['active_contract_digest'] != check_sha(contract_digest):
                raise Invalid('REVISION_CONFLICT', 'stale feature contract')
            rows = self.db.read('SELECT * FROM v1_artifact_manifests WHERE feature_id=? ORDER BY artifact_id', (feature_id,))
            records = [json.loads(row['payload_json']) for row in rows]
            for item in records:
                check_lineage(run, item)
                verified_bytes(self.cas, item['byte_proof_ref'], item['content_sha256'])
            return Result('OK', 'OK', {'feature_id': feature_id, 'manifest_digest': digest(records),
                                      'artifacts': records})
        except Invalid as exc:
            return failure(exc)
