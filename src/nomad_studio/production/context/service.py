"""R9 ContextPort: immutable exact-bible and source snapshots, no bible edits."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .core import (Result, SourceFence, Invalid, check_id, check_sha, check_lineage,
                   digest, bytes_digest, failure, json_bytes, row_one, run_record, store_bytes, verified_bytes)


class ContextService:
    def __init__(self, db: Any, cas: Any, bibles: Any, source: Any, quality: Any, clock_us: Any):
        self.db, self.cas, self.bibles, self.source, self.quality, self.clock_us = (
            db, cas, bibles, source, quality, clock_us)

    def build_bundle(self, project_id: str, brief_id: str, bible_pair_digest: str,
                     source_fence: SourceFence | None) -> Result:
        try:
            check_id(project_id)
            check_id(brief_id)
            check_sha(bible_pair_digest)
            brief = row_one(self.db, 'SELECT * FROM v1_briefs WHERE brief_id=? AND project_id=?',
                            (brief_id, project_id))
            if brief is None:
                raise Invalid('NOT_FOUND', 'brief not found in project')
            run = row_one(self.db, 'SELECT * FROM v1_feature_runs WHERE feature_id=? AND project_id=?',
                          (brief['feature_id'], project_id))
            if run is not None and run['bible_pair_digest'] != bible_pair_digest:
                raise Invalid('STALE_BIBLE', 'feature bible pair has changed')
            editions = self.db.read('SELECT * FROM v1_bible_editions WHERE project_id=? AND active=1',
                                    (project_id,))
            editions_by_kind = {row['kind']: dict(row) for row in editions}
            if len(editions) != 2 or set(editions_by_kind) != {'DESIGN', 'TECHNICAL'}:
                raise Invalid('STALE_BIBLE', 'two active bible editions required')
            pair = self.bibles.active_pair(project_id)
            if pair.get('pair_digest') != bible_pair_digest or pair.get('project_id') != project_id:
                raise Invalid('STALE_BIBLE', 'active pair digest not verified')
            excerpts = []
            pins = {}
            for kind, prefix in (('DESIGN', 'design'), ('TECHNICAL', 'technical')):
                row = editions_by_kind[kind]
                if (pair.get(prefix + '_edition_id') != row['edition_id'] or
                    pair.get(prefix + '_manifest_digest') != row['manifest_digest'] or
                    pair.get(prefix + '_epoch') != row['epoch']):
                    raise Invalid('STALE_BIBLE', 'edition pin differs from trusted state')
                pins[prefix] = {'edition_id': row['edition_id'], 'epoch': row['epoch'],
                                'manifest_digest': check_sha(row['manifest_digest']),
                                'source_commit': check_sha(row['source_commit'], commit=True)}
                clauses = self.db.read('SELECT * FROM v1_bible_clauses WHERE edition_id=?', (row['edition_id'],))
                for clause in clauses:
                    observed = self.bibles.excerpt(row['edition_id'], clause['clause_id'])
                    if (observed.get('kind') != clause['kind'] or observed.get('scope') != clause['scope']
                        or bytes_digest(observed['text'].encode('utf-8')) != clause['content_digest']):
                        raise Invalid('EVIDENCE_INVALID', 'bible excerpt byte proof invalid')
                    excerpts.append({'ref': prefix + '/' + clause['clause_id'],
                                     'kind': clause['kind'], 'scope': clause['scope'],
                                     'text': observed['text'], 'content_digest': clause['content_digest']})
            if source_fence is not None:
                if (getattr(source_fence, 'project_id', None) != project_id
                        or source_fence.feature_id != brief['feature_id']
                        or source_fence.bible_pair_digest != bible_pair_digest):
                    raise Invalid('WRONG_FEATURE', 'invalid source context fence')
                if self.source.project_id(source_fence.repo) != project_id:
                    raise Invalid('WRONG_PROJECT', 'source fence repository project mismatch')
                head = self.source.head(source_fence.repo, source_fence.branch)
                if head != source_fence.expected_parent_commit:
                    raise Invalid('SOURCE_CONFLICT', 'source branch head differs from source fence')
                source_sha = check_sha(head, commit=True)
                source_ref = {'repo': source_fence.repo, 'branch': source_fence.branch,
                              'source_sha': source_sha, 'resource_scope_digest': source_fence.resource_scope_digest}
            else:
                source_ref = self.source.project_snapshot(project_id, brief['feature_id'])
                source_sha = check_sha(source_ref['source_sha'], commit=True)
            bundle = {'project_id': project_id, 'feature_id': brief['feature_id'],
                      'brief_id': brief_id, 'original_brief_digest': brief['original_digest'],
                      'bible_pair_digest': bible_pair_digest,
                      'feature_contract_digest': run['active_contract_digest'] if run is not None else None,
                      'graph_revision': run['graph_revision'] if run is not None else 0, 'source': source_ref,
                      'source_sha': source_sha, 'bible_pins': pins,
                      'canonical_excerpts': sorted(excerpts, key=lambda e: e['ref'])}
            sha, ref = store_bytes(self.cas, json_bytes(bundle))
            bundle_id = 'bundle-' + sha
            def insert(tx: Any) -> None:
                old = tx.execute('SELECT * FROM v1_context_bundles WHERE bundle_digest=?', (sha,)).fetchone()
                if old is not None:
                    if old['content_ref'] != ref or old['feature_id'] != brief['feature_id']:
                        raise Invalid('EVIDENCE_INVALID', 'context digest collision')
                    return
                tx.execute('INSERT INTO v1_context_bundles (feature_id,bundle_id,bible_pair_digest,source_digest,bundle_digest,content_ref) '
                           'VALUES (?,?,?,?,?,?)', (brief['feature_id'], bundle_id, bible_pair_digest,
                                                    digest(source_ref), sha, ref))
            # v1_context_bundles has an FK to v1_feature_runs: a pre-contract
            # bundle cannot be inserted before the run exists. The immutable
            # byte-backed CAS record is the admissible pre-contract source.
            if run is not None:
                self.db.write(insert)
            return Result('OK', 'OK', {'project_id': project_id,
                                      'brief_id': brief_id, 'feature_id': brief['feature_id'],
                                      'bible_pair_digest': bible_pair_digest,
                                      'bundle_digest': sha, 'bundle_id': bundle_id,
                                      'bundle_ref': ref, 'source_sha': source_sha,
                                      'source': source_ref, 'bible_pins': pins,
                                      'canonical_excerpts': bundle['canonical_excerpts'],
                                      'excerpt_count': len(excerpts)}, bundle_id, (ref,))
        except Invalid as exc:
            return failure(exc)

    def get_verified_excerpt(self, bundle_digest: str, excerpt_ref: str) -> Result:
        try:
            check_sha(bundle_digest)
            row = row_one(self.db, 'SELECT * FROM v1_context_bundles WHERE bundle_digest=?', (bundle_digest,))
            if row is None:
                raise Invalid('NOT_FOUND', 'bundle not found')
            bundle = json.loads(verified_bytes(self.cas, row['content_ref'], bundle_digest))
            matches = [x for x in bundle['canonical_excerpts'] if x['ref'] == excerpt_ref]
            if len(matches) != 1:
                raise Invalid('NOT_FOUND', 'canonical excerpt not found')
            excerpt = matches[0]
            if bytes_digest(excerpt['text'].encode('utf-8')) != excerpt['content_digest']:
                raise Invalid('EVIDENCE_INVALID', 'content digest mismatch')
            return Result('OK', 'OK', excerpt, row['bundle_id'], (row['content_ref'],))
        except Invalid as exc:
            return failure(exc)

    def apply_accepted_feature_outcomes(self, feature_id: str, quality_receipt: Mapping) -> Result:
        try:
            run = run_record(self.db, feature_id)
            if not isinstance(quality_receipt, Mapping) or set(quality_receipt) != {'receipt_id'}:
                raise Invalid('INVALID_INPUT', 'only an immutable receipt reference may request canon projection')
            ref = check_id(quality_receipt['receipt_id'])
            evidence = self.quality.accepted_outcome(feature_id, ref)
            if not isinstance(evidence, Mapping) or evidence.get('acceptance_ref') != ref:
                raise Invalid('EVIDENCE_INVALID', 'outcome has no trusted acceptance proof')
            check_lineage(run, evidence)
            commit = check_sha(evidence['source_commit'], commit=True)
            canonical = check_sha(evidence['canon_digest'])
            outcome_id = 'outcome-' + digest({'feature_id': feature_id, 'acceptance_ref': ref})
            def insert(tx: Any) -> None:
                old = tx.execute('SELECT * FROM v1_canon_outcomes WHERE outcome_id=?', (outcome_id,)).fetchone()
                if old:
                    if old['canon_digest'] != canonical or old['source_commit'] != commit:
                        raise Invalid('IDEMPOTENCY_CONFLICT', 'acceptance receipt changed meaning')
                    return
                tx.execute('INSERT INTO v1_canon_outcomes (feature_id,outcome_id,acceptance_ref,canon_digest,source_commit,status) '
                           'VALUES (?,?,?,?,?,?)', (feature_id, outcome_id, ref, canonical, commit, 'ACCEPTED'))
            self.db.write(insert)
            return Result('OK', 'OK', {'outcome_id': outcome_id, 'canon_digest': canonical}, outcome_id, (ref,))
        except Invalid as exc:
            return failure(exc)
