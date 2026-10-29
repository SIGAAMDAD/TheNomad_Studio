"""Provider adapters over injected credential-isolated transports.

All remote reads return bytes, not a self-attested digest or publication receipt.
The transports MUST provide atomic CAS; a plain GitHub REST ref PATCH is unsafe.
"""
from __future__ import annotations
from typing import Any, Mapping
from .model import (Observation, ProviderConflict, ProviderUnknown,
                    ProviderForbidden, ProviderRateLimited, digest)


def _bytes(raw: object, expected_digest: str, expected_size: int) -> bool:
    return isinstance(raw, bytes) and len(raw) == expected_size and digest(raw) == expected_digest


def marker(effect_key: str) -> str:
    # The stable SHA-256 avoids injecting arbitrary comment delimiters into PR text.
    return '<!-- nomad-publication:' + digest(effect_key.encode('utf-8')) + ' -->'


class GitHubAdapter:
    """Transport methods: read_ref, compare_and_swap_ref, read_commit_artifact,
    read_commit_effect_key, find_pull_requests, create_pull_request.
    compare_and_swap_ref MUST use GitHub GraphQL updateRefs(refUpdates:[{beforeOid, afterOid, force:false}])
    or a provably equivalent atomic compare-and-swap, never REST PATCH.
    """
    def __init__(self, transport: object): self.transport = transport

    def _commit_matches(self, p: Mapping[str, Any], sha: str) -> bool:
        t = p['target']
        raw = self.transport.read_commit_artifact(t['repository'], sha, t['manifest_path'])
        if not _bytes(raw, p['artifact_sha256'], p['artifact_size']): return False
        return self.transport.read_commit_effect_key(t['repository'], sha) == p['effect_key']

    def observe(self, p: Mapping[str, Any]) -> Observation:
        t = p['target']
        try:
            if p['action'] == 'update_ref':
                ref = self.transport.read_ref(t['repository'], t['ref'])
                if ref is None: return Observation('UNKNOWN', detail='ref_unavailable')
                if ref == p['expected_revision']: return Observation('ABSENT_SAFE', revision=ref)
                if ref != t['proposed_revision']: return Observation('CONFLICT', revision=ref)
                if self._commit_matches(p, ref): return Observation('APPLIED', revision=ref)
                return Observation('CONFLICT', revision=ref, detail='content_or_marker_mismatch')
            prs = self.transport.find_pull_requests(t['repository'], t['head_ref'], t['base_ref'])
            if prs is None: return Observation('UNKNOWN', detail='pr_index_unavailable')
            matching = [x for x in prs if marker(p['effect_key']) in x.get('body','')]
            if len(matching) > 1: return Observation('CONFLICT', detail='duplicate_pr_marker')
            if not matching: return Observation('ABSENT_UNSAFE', detail='pr_index_may_lag')
            pr = matching[0]
            if pr.get('head_revision') != t['head_revision'] or not self._commit_matches(p, t['head_revision']):
                return Observation('CONFLICT', detail='pr_revision_or_content_mismatch')
            return Observation('APPLIED', revision=str(pr['id']))
        except (ProviderConflict, ProviderForbidden): raise
        except Exception as exc:
            # Network exceptions, including timeouts, cannot prove non-execution.
            raise ProviderUnknown('remote_readback_unavailable') from exc

    def publish(self, p: Mapping[str, Any]) -> object:
        t = p['target']
        try:
            if p['action'] == 'update_ref':
                if not self._commit_matches(p, t['proposed_revision']):
                    raise ProviderConflict('immutable_artifact_or_marker_invalid')
                current = self.transport.read_ref(t['repository'], t['ref'])
                if current != p['expected_revision']: raise ProviderConflict('ref_mismatch')
                return self.transport.compare_and_swap_ref(t['repository'], t['ref'],
                         p['expected_revision'], t['proposed_revision'], force=False)
            if not self._commit_matches(p, t['head_revision']):
                raise ProviderConflict('immutable_artifact_or_marker_invalid')
            if self.transport.read_ref(t['repository'], t['head_ref']) != t['head_revision']:
                raise ProviderConflict('head_mismatch')
            prs = self.transport.find_pull_requests(t['repository'], t['head_ref'], t['base_ref'])
            if prs is None: raise ProviderUnknown('pr_index_unavailable')
            if any(marker(p['effect_key']) in x.get('body','') for x in prs):
                raise ProviderConflict('effect_already_visible_reconcile_first')
            return self.transport.create_pull_request(t['repository'], t['head_ref'], t['base_ref'],
                         t['title'], t['body'] + '\n' + marker(p['effect_key']))
        except (ProviderConflict, ProviderForbidden, ProviderRateLimited, ProviderUnknown): raise
        except Exception as exc: raise ProviderUnknown('github_effect_ambiguous') from exc


class DriveAdapter:
    """Transport methods: read_file, export_file, replace_if_revision.

    atomic_revision_cas=True means an *actual* conditional revision primitive is used,
    not a read-then-write check. No assumption is made about a Drive API capability.
    """
    def __init__(self, transport: object, artifact_reader: object, max_bytes: int):
        self.transport = transport
        self.artifact_reader = artifact_reader
        self.max_bytes = max_bytes

    def _artifact(self, p: Mapping[str, Any]) -> bytes:
        if p['artifact_size'] > self.max_bytes: raise ProviderForbidden('artifact_size_limit')
        data = self.artifact_reader.read(p['artifact_ref'])
        if not _bytes(data, p['artifact_sha256'], p['artifact_size']):
            raise ProviderConflict('local_artifact_digest_mismatch')
        return data

    def observe(self, p: Mapping[str, Any]) -> Observation:
        t = p['target']
        try:
            meta = self.transport.read_file(t['file_id'])
            if meta is None: return Observation('UNKNOWN', detail='drive_file_unavailable')
            if t['parent_id'] not in meta.get('parents', ()):
                return Observation('CONFLICT', detail='parent_scope_changed')
            rev = str(meta.get('revision', ''))
            if not rev: return Observation('UNKNOWN', detail='revision_unavailable')
            if rev == p['expected_revision']: return Observation('ABSENT_SAFE', revision=rev)
            if meta.get('effect_key') != p['effect_key']:
                return Observation('CONFLICT', revision=rev, detail='effect_key_mismatch')
            content = self.transport.export_file(t['file_id'], t['mime_type'])
            if not _bytes(content, p['artifact_sha256'], p['artifact_size']):
                return Observation('CONFLICT', revision=rev, detail='exported_bytes_mismatch')
            # Ensure revision did not change between metadata and content reads.
            meta2 = self.transport.read_file(t['file_id'])
            if meta2 is None or str(meta2.get('revision', '')) != rev:
                return Observation('UNKNOWN', detail='revision_changed_during_readback')
            if t['parent_id'] not in meta2.get('parents', ()) or meta2.get('effect_key') != p['effect_key']:
                return Observation('UNKNOWN', detail='metadata_changed_during_readback')
            return Observation('APPLIED', revision=rev)
        except (ProviderConflict, ProviderForbidden): raise
        except Exception as exc: raise ProviderUnknown('drive_readback_unavailable') from exc

    def publish(self, p: Mapping[str, Any]) -> object:
        if getattr(self.transport, 'atomic_revision_cas', False) is not True:
            raise ProviderForbidden('atomic_drive_cas_not_available')
        t = p['target']
        data = self._artifact(p)
        try:
            meta = self.transport.read_file(t['file_id'])
            if meta is None or t['parent_id'] not in meta.get('parents', ()):
                raise ProviderConflict('parent_scope_missing')
            if str(meta.get('revision', '')) != p['expected_revision']:
                raise ProviderConflict('revision_mismatch')
            return self.transport.replace_if_revision(
                t['file_id'], p['expected_revision'], data, t['parent_id'],
                t['mime_type'], p['effect_key'])
        except (ProviderConflict, ProviderForbidden, ProviderRateLimited, ProviderUnknown): raise
        except Exception as exc: raise ProviderUnknown('drive_effect_ambiguous') from exc
