"""R9 ProjectAdapterPort: exact source/adapter/toolchain-fenced execution.

The adapter resolver reads a pinned Technical Bible and source provider. The
trusted runner executes an actual job; a simulated fixture can exercise the
protocol but never supplies a production acceptance receipt.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .core import (Result, Invalid, check_id, check_sha, digest, failure,
                   json_bytes, row_one, run_record, store_bytes, verified_bytes)


class ProjectAdapterService:
    def __init__(self, db: Any, cas: Any, bibles: Any, source: Any,
                 registry: Any, runner: Any, clock_us: Any):
        self.db, self.cas, self.bibles, self.source = db, cas, bibles, source
        self.registry, self.runner, self.clock_us = registry, runner, clock_us
        self._pure_cache: dict[str, Mapping] = {}

    def resolve_adapter(self, project_id: str, technical_edition_id: str,
                        pinned_source_commit: str) -> Result:
        try:
            check_id(project_id)
            check_id(technical_edition_id)
            check_sha(pinned_source_commit, commit=True)
            row = row_one(self.db, 'SELECT * FROM v1_bible_editions WHERE project_id=? AND kind=? AND edition_id=? AND active=1',
                          (project_id, 'TECHNICAL', technical_edition_id))
            if row is None:
                raise Invalid('STALE_BIBLE', 'technical edition is not active')
            if not self.bibles.verify_edition(row):
                raise Invalid('EVIDENCE_INVALID', 'unverified technical bible')
            config = self.registry.resolve(project_id, technical_edition_id)
            if not isinstance(config, Mapping) or not set(config) == {'adapter_id', 'adapter_digest', 'toolchain_digest', 'source_repo'}:
                raise Invalid('INVALID_INPUT', 'adapter registry missing exact pin fields')
            check_id(config['adapter_id'])
            check_sha(config['adapter_digest'])
            check_sha(config['toolchain_digest'])
            if not self.source.verify_commit(config['source_repo'], pinned_source_commit):
                raise Invalid('SOURCE_CONFLICT', 'source commit has no provider readback')
            record = {'project_id': project_id, 'technical_edition_id': technical_edition_id,
                      'technical_epoch': row['epoch'], 'manifest_digest': row['manifest_digest'],
                      'pinned_source_commit': pinned_source_commit, **dict(config)}
            sha, ref = store_bytes(self.cas, json_bytes(record))
            archive_id = 'adapter-' + sha
            def insert(tx: Any) -> None:
                old = tx.execute('SELECT content_digest FROM v1_archive_refs WHERE archive_id=?', (archive_id,)).fetchone()
                if old is None:
                    tx.execute('INSERT INTO v1_archive_refs (archive_id,feature_id,content_digest,storage_ref,created_at_us) '
                               'VALUES (?,NULL,?,?,?)', (archive_id, sha, ref, int(self.clock_us())))
                elif old['content_digest'] != sha:
                    raise Invalid('EVIDENCE_INVALID', 'adapter pin collision')
            self.db.write(insert)
            return Result('OK', 'OK', {'adapter_ref': archive_id, 'adapter_digest': config['adapter_digest'],
                                      'toolchain_digest': config['toolchain_digest']}, archive_id, (ref,))
        except Invalid as exc:
            return failure(exc)

    def _load(self, project_id: str, branch_commit: str, adapter_ref: str) -> dict:
        check_id(project_id)
        check_sha(branch_commit, commit=True)
        check_id(adapter_ref)
        row = row_one(self.db, 'SELECT * FROM v1_archive_refs WHERE archive_id=? AND feature_id IS NULL',
                      (adapter_ref,))
        if row is None:
            raise Invalid('NOT_FOUND', 'adapter not resolved')
        config = json.loads(verified_bytes(self.cas, row['storage_ref'], row['content_digest']))
        if config['project_id'] != project_id:
            raise Invalid('WRONG_PROJECT', 'adapter belongs to another project')
        edition = row_one(self.db, 'SELECT * FROM v1_bible_editions WHERE edition_id=? AND project_id=? AND active=1',
                          (config['technical_edition_id'], project_id))
        if edition is None or edition['epoch'] != config['technical_epoch'] or edition['manifest_digest'] != config['manifest_digest']:
            raise Invalid('STALE_BIBLE', 'adapter technical bible pin changed')
        if not self.source.verify_commit(config['source_repo'], branch_commit):
            raise Invalid('SOURCE_CONFLICT', 'unverified branch commit')
        return config

    def _run(self, operation: str, project_id: str, branch_commit: str, adapter_ref: str,
             trusted_runner: Mapping, *, feature_id: str | None = None,
             check_ids: Sequence[str] = (), asset_manifest_ref: str = '') -> Result:
        try:
            config = self._load(project_id, branch_commit, adapter_ref)
            if feature_id is not None:
                run = run_record(self.db, check_id(feature_id), project_id)
                if not run['graph_revision'] or not run['bible_pair_digest']:
                    raise Invalid('REVISION_CONFLICT', 'feature cannot be verified')
            if not isinstance(trusted_runner, Mapping) or set(trusted_runner) != {'runner_ref', 'runner_proof_ref'}:
                raise Invalid('EVIDENCE_INVALID', 'trusted runner proof missing')
            check_id(trusted_runner['runner_ref'])
            check_id(trusted_runner['runner_proof_ref'])
            if not self.runner.authenticate(trusted_runner['runner_ref'], trusted_runner['runner_proof_ref']):
                raise Invalid('EVIDENCE_INVALID', 'untrusted execution actor')
            if not isinstance(check_ids, (list, tuple)) or len(check_ids) > 1024:
                raise Invalid('INVALID_INPUT', 'check IDs must be bounded')
            for cid in check_ids:
                check_id(cid)
            if operation == 'assets':
                check_id(asset_manifest_ref)
            params = {'operation': operation, 'project_id': project_id, 'feature_id': feature_id,
                      'branch_commit': branch_commit, 'adapter_ref': adapter_ref,
                      'adapter_digest': config['adapter_digest'],
                      'toolchain_digest': config['toolchain_digest'],
                      'runner_ref': trusted_runner['runner_ref'],
                      'check_ids': list(check_ids), 'asset_manifest_ref': asset_manifest_ref}
            key = digest(params)
            # Cached results require fresh runner-signed provenance and immutable log-byte
            # readback; a cache hit never manufactures a new QA/provider receipt.
            cached = self._pure_cache.get(key)
            if (cached is not None and callable(getattr(self.runner, 'verify_cached_receipt', None))
                    and self.runner.verify_cached_receipt(cached, params, trusted_runner)):
                verified_bytes(self.cas, cached['log_ref'], cached['log_digest'])
                return Result('OK', 'OK', dict(cached), 'test-' + key, (cached['log_ref'],))
            observed = self.runner.execute(params)
            if (not isinstance(observed, Mapping) or set(observed) != {'exit_code', 'log_bytes', 'runner_ref',
                    'adapter_digest', 'toolchain_digest', 'input_digest', 'observed_at_us', 'simulated'}
                or type(observed['exit_code']) is not int or observed['exit_code'] < 0
                or observed['runner_ref'] != trusted_runner['runner_ref']
                or observed['adapter_digest'] != config['adapter_digest']
                or observed['toolchain_digest'] != config['toolchain_digest']
                or observed['input_digest'] != key
                or not isinstance(observed['log_bytes'], bytes)
                or type(observed['observed_at_us']) is not int or observed['observed_at_us'] <= 0):
                raise Invalid('EVIDENCE_INVALID', 'actual runner output provenance incorrect')
            log_sha, log_ref = store_bytes(self.cas, observed['log_bytes'])
            evidence = {'test_id': operation, 'commit': branch_commit,
                        'exit_code': observed['exit_code'], 'log_digest': log_sha,
                        'trusted_runner_ref': observed['runner_ref'],
                        'observed_at_us': observed['observed_at_us'],
                        'adapter_digest': config['adapter_digest'],
                        'toolchain_digest': config['toolchain_digest'], 'input_digest': key,
                        'log_ref': log_ref, 'source_verified': True}
            if observed['exit_code']:
                return Result('REJECTED', 'TEST_FAILED', evidence, evidence_refs=(log_ref,))
            if observed['simulated'] is True:
                return Result('NOT_VERIFIED', 'PROVIDER_NOT_VERIFIED', evidence, evidence_refs=(log_ref,))
            if observed['simulated'] is not False:
                raise Invalid('EVIDENCE_INVALID', 'unknown runner proof classification')
            if not self.runner.verify_native(observed, trusted_runner):
                raise Invalid('EVIDENCE_INVALID', 'native runner proof failed')
            self._pure_cache[key] = evidence
            return Result('OK', 'OK', evidence, 'test-' + key, (log_ref,))
        except Invalid as exc:
            return failure(exc)
        except (OSError, TimeoutError):
            return Result('NOT_VERIFIED', 'TEST_NOT_RUN')

    def run_import(self, project_id: str, branch_commit: str, adapter_ref: str,
                   trusted_runner: Mapping) -> Result:
        return self._run('import', project_id, branch_commit, adapter_ref, trusted_runner)

    def run_build(self, project_id: str, branch_commit: str, adapter_ref: str,
                  trusted_runner: Mapping) -> Result:
        return self._run('build', project_id, branch_commit, adapter_ref, trusted_runner)

    def run_feature_smoke(self, feature_id: str, branch_commit: str, check_ids: Sequence[str],
                          adapter_ref: str, trusted_runner: Mapping) -> Result:
        try:
            run = run_record(self.db, feature_id)
        except Invalid as exc:
            return failure(exc)
        return self._run('smoke', run['project_id'], branch_commit, adapter_ref, trusted_runner,
                         feature_id=feature_id, check_ids=check_ids)

    def verify_assets(self, feature_id: str, branch_commit: str, asset_manifest_ref: str,
                      adapter_ref: str, trusted_runner: Mapping) -> Result:
        try:
            run = run_record(self.db, feature_id)
        except Invalid as exc:
            return failure(exc)
        row = row_one(self.db, 'SELECT * FROM v1_artifact_manifests WHERE feature_id=? AND artifact_id=?',
                      (feature_id, asset_manifest_ref))
        if row is None:
            return Result('NOT_FOUND', 'NOT_FOUND')
        try:
            verified_bytes(self.cas, row['byte_proof_ref'], row['content_digest'])
        except Invalid as exc:
            return failure(exc)
        return self._run('assets', run['project_id'], branch_commit, adapter_ref, trusted_runner,
                         feature_id=feature_id, asset_manifest_ref=asset_manifest_ref)
