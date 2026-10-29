"""R9 RepositoryPort: scoped feature-branch patches, atomic prefix leases, CAS readback.

Provider is an authenticated conditional-commit adapter; it MUST reject stale
expected-parent commits. The service never requests a protected branch mutation.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from nomad_studio.production.context.core import (
    Result, SourceFence, Invalid, check_id, check_sha, digest, failure, json_bytes,
    normalize_path, overlapping, row_one, run_record, store_bytes, verified_bytes,
)


class RepositoryService:
    def __init__(self, db: Any, cas: Any, provider: Any, scope: Any,
                 reviewer: Any, clock_us: Any, *, lease_duration_us: int = 600_000_000):
        self.db, self.cas, self.provider, self.scope = db, cas, provider, scope
        self.reviewer, self.clock_us = reviewer, clock_us
        self.lease_duration_us = lease_duration_us
        if lease_duration_us < 1:
            raise ValueError('lease duration must be positive')

    def _fence(self, feature_id: str, fence: SourceFence) -> dict:
        if getattr(fence, 'feature_id', None) != feature_id:
            raise Invalid('WRONG_FEATURE', 'incorrect source fence')
        run = run_record(self.db, feature_id, fence.project_id)
        if run['bible_pair_digest'] != fence.bible_pair_digest:
            raise Invalid('STALE_BIBLE', 'outdated source fence')
        check_sha(fence.resource_scope_digest)
        check_sha(fence.expected_parent_commit, commit=True)
        if (not isinstance(fence.repo, str) or re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', fence.repo) is None
                or not isinstance(fence.branch, str) or
                re.fullmatch(r'(?:refs/heads/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', fence.branch) is None
                or any(part in ('.', '..') or part.endswith('.lock') for part in fence.branch.split('/'))
                or '@{' in fence.branch):
            raise Invalid('INVALID_INPUT', 'invalid canonical repository or branch')
        if self.provider.project_id(fence.repo) != fence.project_id:
            raise Invalid('WRONG_PROJECT', 'repository project mismatch')
        if self.provider.is_protected(fence.repo, fence.branch):
            raise Invalid('SCOPE_EXCEEDED', 'protected branch is not a feature write target')
        return run

    def _head(self, fence: SourceFence) -> None:
        if self.provider.head(fence.repo, fence.branch) != fence.expected_parent_commit:
            raise Invalid('SOURCE_CONFLICT', 'expected branch parent SHA differs')

    def prepare_feature_patch(self, feature_id: str, patches: Sequence[Mapping], fence: SourceFence) -> Result:
        try:
            run = self._fence(feature_id, fence)
            if not patches or len(patches) > 1024:
                raise Invalid('INVALID_INPUT', 'invalid patch count')
            normalized = []
            keys: set[str] = set()
            for patch in patches:
                if not isinstance(patch, Mapping) or set(patch) != {'resource_path', 'content_sha256', 'byte_proof_ref'}:
                    raise Invalid('INVALID_INPUT', 'patch requires exact byte proof fields')
                path, path_key = normalize_path(patch['resource_path'])
                if path_key in keys:
                    raise Invalid('LEASE_CONFLICT', 'duplicate normalized patch path')
                keys.add(path_key)
                if not self.scope.allows(run, fence, path):
                    raise Invalid('SCOPE_EXCEEDED', 'path outside canonical feature contract')
                expected = check_sha(patch['content_sha256'])
                verified_bytes(self.cas, patch['byte_proof_ref'], expected)
                normalized.append({'resource_path': path, 'resource_path_key': path_key,
                                   'content_sha256': expected, 'byte_proof_ref': patch['byte_proof_ref']})
            normalized.sort(key=lambda item: item['resource_path_key'])
            for i, item in enumerate(normalized):
                if any(overlapping(item['resource_path_key'], other['resource_path_key']) for other in normalized[:i]):
                    raise Invalid('LEASE_CONFLICT', 'nested paths within one patch')
            self._head(fence)
            scope = {'project_id': fence.project_id, 'feature_id': feature_id,
                     'repo': fence.repo, 'branch': fence.branch,
                     'expected_parent_commit': fence.expected_parent_commit,
                     'resource_scope_digest': fence.resource_scope_digest,
                     'bible_pair_digest': fence.bible_pair_digest,
                     'feature_contract_digest': run['active_contract_digest'],
                     'graph_revision': run['graph_revision']}
            bundle = {'scope': scope, 'patches': normalized}
            if not self.scope.verify_interface(run, fence, bundle):
                raise Invalid('SCOPE_EXCEEDED', 'patch violates feature interface/bridge contract')
            bundle_data = json_bytes(bundle)
            patch_digest, byte_ref = store_bytes(self.cas, bundle_data)
            # One logical request for a feature+fence+same normalized paths.
            logical = digest({'scope': scope, 'paths': sorted(keys)})
            archive_id = 'patch-' + logical
            lease_ids = {item['resource_path_key']: 'lease-' + digest({
                'request': logical, 'path': item['resource_path_key']}) for item in normalized}
            now = int(self.clock_us())

            def reserve(tx: Any) -> bool:
                latest = tx.execute('SELECT active_contract_digest,bible_pair_digest,graph_revision FROM v1_feature_runs WHERE feature_id=?',
                                    (feature_id,)).fetchone()
                if (latest is None or latest['active_contract_digest'] != run['active_contract_digest'] or
                    latest['bible_pair_digest'] != fence.bible_pair_digest or
                    latest['graph_revision'] != run['graph_revision']):
                    raise Invalid('REVISION_CONFLICT', 'feature changed during lease reservation')
                tx.execute('UPDATE v1_source_leases SET active=0 WHERE repo=? AND branch=? AND active=1 AND expires_at_us<=?',
                           (fence.repo, fence.branch, now))
                existing = tx.execute('SELECT content_digest FROM v1_archive_refs WHERE archive_id=?', (archive_id,)).fetchone()
                if existing is not None:
                    if existing['content_digest'] != patch_digest:
                        raise Invalid('IDEMPOTENCY_CONFLICT', 'changed bytes on same patch intent')
                    return False
                leases = tx.execute('SELECT lease_id,resource_path_key FROM v1_source_leases '
                                    'WHERE repo=? AND branch=? AND active=1 AND expires_at_us>?',
                                    (fence.repo, fence.branch, now)).fetchall()
                for item in normalized:
                    if any(overlapping(item['resource_path_key'], lease['resource_path_key']) for lease in leases):
                        raise Invalid('LEASE_CONFLICT', 'active exact or ancestor/descendant lease')
                for item in normalized:
                    tx.execute('INSERT INTO v1_source_leases '
                               '(feature_id,lease_id,repo,branch,resource_path,resource_path_key,expected_parent_commit,active,expires_at_us) '
                               'VALUES (?,?,?,?,?,?,?,?,?)',
                               (feature_id, lease_ids[item['resource_path_key']], fence.repo, fence.branch,
                                item['resource_path'], item['resource_path_key'], fence.expected_parent_commit,
                                1, now + self.lease_duration_us))
                tx.execute('INSERT INTO v1_archive_refs (archive_id,feature_id,content_digest,storage_ref,created_at_us) '
                           'VALUES (?,?,?,?,?)', (archive_id, feature_id, patch_digest, byte_ref, now))
                return True
            self.db.write(reserve)
            return Result('OK', 'OK', {'patch_digest': patch_digest, 'archive_id': archive_id,
                                       'lease_ids': lease_ids, 'source_parent_commit': fence.expected_parent_commit},
                          archive_id, (byte_ref,))
        except Invalid as exc:
            return failure(exc)

    def integrate_feature(self, feature_id: str, graph_revision: int, reviewed_artifacts: Mapping,
                          fence: SourceFence) -> Result:
        try:
            run = self._fence(feature_id, fence)
            if type(graph_revision) is not int or graph_revision != run['graph_revision']:
                raise Invalid('REVISION_CONFLICT', 'graph revision mismatched')
            if not isinstance(reviewed_artifacts, Mapping) or set(reviewed_artifacts) != {'archive_id', 'patch_digest', 'review_ref'}:
                raise Invalid('INVALID_INPUT', 'need exact reviewed patch identity')
            archive_id = check_id(reviewed_artifacts['archive_id'])
            patch_digest = check_sha(reviewed_artifacts['patch_digest'])
            row = row_one(self.db, 'SELECT * FROM v1_archive_refs WHERE archive_id=? AND feature_id=?',
                          (archive_id, feature_id))
            if row is None or row['content_digest'] != patch_digest:
                raise Invalid('EVIDENCE_INVALID', 'reviewed patch differs from stored bytes')
            bundle = json.loads(verified_bytes(self.cas, row['storage_ref'], patch_digest))
            scoped = bundle['scope']
            if scoped != {'project_id': fence.project_id, 'feature_id': feature_id, 'repo': fence.repo,
                          'branch': fence.branch, 'expected_parent_commit': fence.expected_parent_commit,
                          'resource_scope_digest': fence.resource_scope_digest,
                          'bible_pair_digest': fence.bible_pair_digest,
                          'feature_contract_digest': run['active_contract_digest'],
                          'graph_revision': graph_revision}:
                raise Invalid('REVISION_CONFLICT', 'stale patch scope')
            if not self.scope.verify_interface(run, fence, bundle):
                raise Invalid('SCOPE_EXCEEDED', 'patch no longer satisfies bridge/interface contract')
            if not self.reviewer.verify(feature_id, patch_digest, reviewed_artifacts['review_ref']):
                raise Invalid('EVIDENCE_INVALID', 'no independent exact-byte review')
            commit_id = 'src-' + digest({'feature_id': feature_id, 'patch_digest': patch_digest})
            committed = row_one(self.db, 'SELECT * FROM v1_source_commits WHERE commit_record_id=?', (commit_id,))
            if committed is not None:
                if (not committed['verified'] or committed['expected_parent_sha'] != fence.expected_parent_commit
                        or committed['repo'] != fence.repo or committed['branch'] != fence.branch):
                    raise Invalid('SOURCE_CONFLICT', 'conflicting previous commit')
                observed = self.provider.readback(fence.repo, fence.branch, committed['provider_receipt_ref'])
                if not observed or observed.get('commit_sha') != committed['commit_sha']:
                    return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
                return Result('OK', 'OK', {'commit_sha': committed['commit_sha'],
                    'repo': fence.repo, 'branch': fence.branch}, commit_id, (committed['provider_receipt_ref'],))
            # Revalidate all leased paths and source inputs before external publication.
            self._head(fence)
            now = int(self.clock_us())
            for item in bundle['patches']:
                if not self.scope.allows(run, fence, item['resource_path']):
                    raise Invalid('SCOPE_EXCEEDED', 'scope changed since preparation')
                verified_bytes(self.cas, item['byte_proof_ref'], item['content_sha256'])
                l_id = 'lease-' + digest({'request': archive_id[6:], 'path': item['resource_path_key']})
                lease = row_one(self.db, 'SELECT * FROM v1_source_leases WHERE lease_id=?', (l_id,))
                if (lease is None or not lease['active'] or lease['expires_at_us'] <= now
                        or lease['feature_id'] != feature_id or lease['expected_parent_commit'] != fence.expected_parent_commit):
                    raise Invalid('LEASE_FENCED', 'missing or expired matching source lease')
            # Provider MUST do expected-parent CAS. No write to protected mainline.
            self._head(fence)
            try:
                receipt = self.provider.commit_feature_branch(fence.repo, fence.branch,
                    fence.expected_parent_commit, bundle['patches'], 'commit-' + patch_digest)
            except (OSError, TimeoutError):
                return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
            if not isinstance(receipt, Mapping) or receipt.get('status') != 'OK':
                return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
            observed = self.provider.readback(fence.repo, fence.branch, receipt['receipt_ref'])
            if (not isinstance(observed, Mapping) or observed.get('commit_sha') != receipt.get('commit_sha')
                    or observed.get('expected_parent_sha') != fence.expected_parent_commit
                    or observed.get('receipt_ref') != receipt.get('receipt_ref')):
                return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
            sha = check_sha(observed['commit_sha'], commit=True)
            def record(tx: Any) -> None:
                old = tx.execute('SELECT * FROM v1_source_commits WHERE commit_record_id=?', (commit_id,)).fetchone()
                if old is not None:
                    if old['commit_sha'] != sha or not old['verified']:
                        raise Invalid('SOURCE_CONFLICT', 'inconsistent provider duplicate result')
                    return
                tx.execute('INSERT INTO v1_source_commits '
                           '(feature_id,commit_record_id,repo,branch,commit_sha,expected_parent_sha,provider_receipt_ref,verified) '
                           'VALUES (?,?,?,?,?,?,?,1)',
                           (feature_id, commit_id, fence.repo, fence.branch, sha,
                            fence.expected_parent_commit, observed['receipt_ref']))
                for item in bundle['patches']:
                    l_id = 'lease-' + digest({'request': archive_id[6:], 'path': item['resource_path_key']})
                    tx.execute('UPDATE v1_source_leases SET active=0 WHERE lease_id=? AND active=1', (l_id,))
            self.db.write(record)
            return Result('OK', 'OK', {'commit_sha': sha, 'repo': fence.repo, 'branch': fence.branch},
                          commit_id, (observed['receipt_ref'],))
        except Invalid as exc:
            return failure(exc)

    def readback_commit(self, effect_ref: str, repo: str, branch: str) -> Result:
        try:
            check_id(effect_ref)
            row = row_one(self.db, 'SELECT * FROM v1_source_commits WHERE commit_record_id=? AND repo=? AND branch=?',
                          (effect_ref, repo, branch))
            if row is None:
                raise Invalid('NOT_FOUND', 'no scoped source commit')
            observation = self.provider.readback(repo, branch, row['provider_receipt_ref'])
            if (not observation or observation.get('commit_sha') != row['commit_sha']
                    or observation.get('expected_parent_sha') != row['expected_parent_sha']):
                return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
            return Result('OK', 'OK', {'commit_sha': row['commit_sha'], 'verified': True},
                          effect_ref, (row['provider_receipt_ref'],))
        except Invalid as exc:
            return failure(exc)
        except (OSError, TimeoutError):
            return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')

    def inspect_branch(self, project_id: str, repo: str, branch: str) -> Result:
        try:
            check_id(project_id)
            if self.provider.project_id(repo) != project_id:
                raise Invalid('WRONG_PROJECT', 'foreign repository')
            head = check_sha(self.provider.head(repo, branch), commit=True)
            return Result('OK', 'OK', {'project_id': project_id, 'repo': repo,
                                        'branch': branch, 'commit_sha': head})
        except Invalid as exc:
            return failure(exc)
        except (OSError, TimeoutError):
            return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
