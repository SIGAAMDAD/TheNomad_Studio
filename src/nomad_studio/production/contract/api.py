"""R9 one-brief -> Bible-locked complete FeatureContract compiler.

The original owner text and both owner-activated Bible edition bytes determine
requirements. Source/context is non-authoritative and may supply only facts.
P2 injects trusted Bible/Authority/Identity ports and the CAB-B-limited writer.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from nomad_studio.production.intake._support import (
    Result, Reject, canonical, check_digest, check_id, check_text, fields, read,
    read_one, reject, result, sha, text_digest, validate_record, write,
)


def default_extract(original_text: str, clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic narrow extraction for explicit feature terms.

    Ambiguous requests fail closed; P2 may inject a semantic extractor which
    still has to submit independently grounded candidates for validation.
    """
    candidates = []
    rules = (
        ('QUEST_GRAPH', r'\bquest|branching objective|mission graph\b', 'quest-graph-replay'),
        ('DIALOGUE', r'\bdialogue|conversation|voice lines\b', 'dialogue-branch-check'),
        ('NARRATIVE', r'\bstory|narrative|lore\b', 'narrative-continuity-check'),
        ('SCENE', r'\bscene|encounter|playable|trigger\b', 'scene-runtime-smoke'),
        ('CODE', r'\bimplement|code|script|gameplay|playable|trigger\b', 'code-build-check'),
        ('ASSET', r'\bmesh|texture|asset|model|material\b', 'asset-import-check'),
        ('AUDIO', r'\baudio|sound|music|sfx\b', 'audio-event-check'),
        ('ANIMATION', r'\banimat|motion|rig\b', 'animation-import-check'),
        ('TEST', r'\btest|verify|smoke|regression\b', 'automated-test-check'),
    )
    for kind, pattern, acceptance in rules:
        match = re.search(pattern, original_text, flags=re.IGNORECASE)
        if match:
            candidates.append({'deliverable_id': kind.lower().replace('_', '-'), 'kind': kind,
                               'description': f'Implement and verify requested {kind.lower().replace("_", " ")} output',
                               'requirement_refs': [], 'acceptance_ids': [acceptance], 'required': True,
                               'source_quotes': [match.group(0)]})
    # Pinned mandatory Bible bytes may require executable output or a test
    # even when a short brief does not explicitly spell out infrastructure.
    for clause in clauses:
        if clause['kind'] != 'REQUIREMENT':
            continue
        for kind, pattern, acceptance in rules:
            if re.search(pattern, clause['text'], flags=re.IGNORECASE):
                # A general Bible rule about quests does not request a quest
                # for an unrelated gameplay feature. It binds a matching output.
                found = next((c for c in candidates if c['kind'] == kind), None)
                if found is None:
                    continue
                if clause['clause_id'] not in found['requirement_refs']:
                    found['requirement_refs'].append(clause['clause_id'])
    # A test is mandatory for each executable request, anchored by the code
    # deliverable and evaluated by Quality's independent required test set.
    executable = any(c['kind'] in {'CODE', 'SCENE', 'QUEST_GRAPH'} for c in candidates)
    if executable and not any(c['kind'] == 'TEST' for c in candidates):
        candidates.append({'deliverable_id': 'test', 'kind': 'TEST',
                           'description': 'Executable feature build/import/smoke regression tests',
                           'requirement_refs': [], 'acceptance_ids': ['automated-test-check'],
                           'required': True, 'source_quotes': []})
        # Anchored to the explicit executable brief, not to unverified source facts.
        candidates[-1]['derived_from_deliverable_id'] = next(c['deliverable_id'] for c in candidates if c['kind'] in {'CODE', 'SCENE', 'QUEST_GRAPH'})
    return candidates


class FeatureContractCompiler:
    def __init__(self, state: Any, bible: Any, authority: Any, identity: Any = None, extractor: Any = None, context_port: Any = None):
        self.state = state
        self.bible = bible
        self.authority = authority
        self.identity = identity
        self.extractor = extractor or default_extract
        self.context_port = context_port

    def current_bible_pair_digest(self, feature_id: str) -> str:
        check_id(feature_id)
        def query(tx):
            row = read_one(tx, 'SELECT bible_pair_digest FROM v1_feature_contracts WHERE feature_id=? ORDER BY revision DESC LIMIT 1', (feature_id,))
            if row is None: reject('NOT_FOUND', 'NOT_FOUND')
            return row['bible_pair_digest']
        return read(self.state, query)

    def _active_bundle(self, project_id: str, feature_id: str, supplied: Mapping[str, Any],
                       context: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        required = {'project_id', 'pair_digest', 'design_edition_id', 'technical_edition_id',
                    'design_manifest_digest', 'technical_manifest_digest', 'design_epoch', 'technical_epoch'}
        pair = fields(supplied, required, required)
        if pair['project_id'] != project_id: reject('WRONG_PROJECT')
        for key in ('pair_digest', 'design_manifest_digest', 'technical_manifest_digest'):
            check_digest(pair[key])
        for key in ('design_edition_id', 'technical_edition_id'): check_id(pair[key])
        if any(type(pair[k]) is not int or pair[k] < 0 for k in ('design_epoch', 'technical_epoch')):
            reject('INVALID_INPUT')
        if self.bible is None or self.authority is None: reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
        actual = self.bible.snapshot_active_pair(project_id, {'project_id': project_id, 'feature_id': feature_id})
        if getattr(actual, 'status', None) != 'OK' or actual.record != pair: reject('STALE_BIBLE')
        observed = self.bible.get_clause_bundle(pair['pair_digest'], {'project_id': project_id, 'feature_id': feature_id})
        if (getattr(observed, 'status', None) != 'OK' or not isinstance(observed.record, Mapping)
            or observed.record.get('project_id') != project_id
            or observed.record.get('bible_pair_digest') != pair['pair_digest']):
            reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
        metadata = observed.record.get('clauses')
        excerpts = context.get('canonical_excerpts')
        pins = context.get('bible_pins')
        if not isinstance(metadata, list) or not isinstance(excerpts, list) or not isinstance(pins, Mapping):
            reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
        for label in ('design', 'technical'):
            pin = pins.get(label)
            if (not isinstance(pin, Mapping) or pin.get('edition_id') != pair[label+'_edition_id']
                or pin.get('epoch') != pair[label+'_epoch']
                or pin.get('manifest_digest') != pair[label+'_manifest_digest']):
                reject('STALE_BIBLE')
        lookup = {item.get('ref'): item for item in excerpts if isinstance(item, Mapping)}
        if len(lookup) != len(excerpts): reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
        clauses = []
        for meta in metadata:
            if not isinstance(meta, Mapping): reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
            label = 'design' if meta.get('bible_kind') == 'DESIGN' else 'technical'
            if meta.get('bible_kind') not in ('DESIGN', 'TECHNICAL'): reject('EVIDENCE_INVALID')
            ref = meta.get('ref')
            expected = label + '/' + str(meta.get('clause_id'))
            excerpt = lookup.get(expected)
            if (not isinstance(excerpt, Mapping) or meta.get('edition_id') != pair[label+'_edition_id']
                or ref != meta.get('edition_id') + ':' + meta.get('clause_id')
                or excerpt.get('kind') != meta.get('kind') or excerpt.get('scope') != meta.get('scope')
                or excerpt.get('content_digest') != meta.get('text_digest')):
                reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
            text = excerpt.get('text')
            if not isinstance(text, str) or text_digest(text) != meta['text_digest']:
                reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
            clauses.append({'clause_id': ref, 'kind': meta['kind'], 'text': text,
                            'text_digest': meta['text_digest'], 'scope': meta['scope'],
                            'precedence': meta['precedence'], 'source_ref': meta['source_ref']})
        if set(lookup) != {('design' if m['bible_kind']=='DESIGN' else 'technical')+'/'+m['clause_id'] for m in metadata}:
            reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
        return pair['pair_digest'], clauses

    def _verified_context(self, project_id: str, brief_id: str, feature_id: str, pair_digest: str,
                          supplied: Mapping[str, Any]) -> None:
        if not isinstance(supplied, Mapping): reject('INVALID_INPUT')
        context = fields(supplied, {'project_id','brief_id','feature_id','bible_pair_digest','bundle_digest','bundle_ref','source_sha','bible_pins','canonical_excerpts','bundle_id','source','excerpt_count','facts','source_metadata'}, {'project_id','brief_id','feature_id','bible_pair_digest','bundle_digest','bundle_ref','source_sha','bible_pins','canonical_excerpts'})
        if context['project_id'] != project_id: reject('WRONG_PROJECT')
        if context['brief_id'] != brief_id or ('feature_id' in context and context['feature_id'] != feature_id):
            reject('WRONG_FEATURE')
        if context['bible_pair_digest'] != pair_digest: reject('STALE_BIBLE')
        if self.context_port is None: reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
        actual = self.context_port.build_bundle(project_id, brief_id, pair_digest, None)
        if getattr(actual, 'status', None) != 'OK' or getattr(actual, 'code', None) != 'OK' or actual.record != context:
            reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')

    @staticmethod
    def _check_active_in_transaction(tx: Any, pair: Mapping[str, Any]) -> None:
        editions = tx.fetch_all('SELECT kind,edition_id,epoch,manifest_digest FROM v1_bible_editions '
                                'WHERE project_id=? AND active=1', (pair['project_id'],))
        if len(editions) != 2:
            reject('STALE_BIBLE')
        active = {e['kind']: dict(e) for e in editions}
        if set(active) != {'DESIGN', 'TECHNICAL'}:
            reject('STALE_BIBLE')
        for kind, label in (('DESIGN', 'design'), ('TECHNICAL', 'technical')):
            edition = active[kind]
            if any((edition['edition_id'] != pair[f'{label}_edition_id'],
                    edition['epoch'] != pair[f'{label}_epoch'],
                    edition['manifest_digest'] != pair[f'{label}_manifest_digest'])):
                reject('STALE_BIBLE')

    def _extract(self, text: str, clauses: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        raw = self.extractor(text, clauses)
        if not isinstance(raw, list) or not raw or len(raw) > 4096: reject('INVALID_INPUT')
        allowed = {c['clause_id']: c for c in clauses if c['kind'] == 'REQUIREMENT'}
        deliverables = []
        seen = set()
        for item in raw:
            candidate = fields(item, {'deliverable_id', 'kind', 'description', 'requirement_refs', 'acceptance_ids', 'required',
                                      'source_quotes', 'derived_from_deliverable_id'},
                               {'deliverable_id', 'kind', 'description', 'requirement_refs', 'acceptance_ids', 'required'})
            quotes = candidate.pop('source_quotes', [])
            derived = candidate.pop('derived_from_deliverable_id', None)
            if not isinstance(quotes, list) or any(not isinstance(q, str) or not q or q.casefold() not in text.casefold() for q in quotes):
                reject('EVIDENCE_INVALID')
            if any(ref not in allowed for ref in candidate['requirement_refs']): reject('EVIDENCE_INVALID')
            # Only owner text or owner-activated mandatory Bible text can mint requirements.
            if not quotes and not candidate['requirement_refs']:
                if derived is None or derived not in seen: reject('EVIDENCE_INVALID')
            deliverable = validate_record('DeliverableRequirement', candidate)
            if not deliverable['acceptance_ids']: reject('INVALID_INPUT')
            if deliverable['deliverable_id'] in seen: reject('INVALID_INPUT')
            seen.add(deliverable['deliverable_id'])
            deliverables.append(deliverable)
        if not any(item['required'] for item in deliverables): reject('INVALID_INPUT')
        ids = sorted({ident for item in deliverables for ident in item['acceptance_ids']})
        if not ids: reject('INVALID_INPUT')
        return deliverables, ids

    def _conflicts(self, project: str, feature: str, brief: str, text: str, deliverables: list[dict[str, Any]],
                   clauses: list[dict[str, Any]], pair_digest: str) -> None:
        decision = {'project_id': project, 'feature_id': feature, 'brief_id': brief,
                    'original_text': text, 'deliverables': deliverables,
                    'mandatory_clause_refs': [c['clause_id'] for c in clauses if c['kind'] == 'REQUIREMENT']}
        outcome = self.authority.assess_bible_conflict(feature, decision, pair_digest)
        if getattr(outcome, 'status', None) is None or getattr(outcome, 'code', None) is None:
            reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
        if outcome.status == 'CONFLICT' and outcome.code == 'BIBLE_CONFLICT':
            violations = outcome.record.get('violations') if isinstance(outcome.record, Mapping) else None
            allowed = set(decision['mandatory_clause_refs'])
            if not isinstance(violations, list) or not violations or any(v not in allowed for v in violations):
                reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
            reject('BIBLE_CONFLICT', 'CONFLICT', {'project_id': project, 'feature_id': feature, 'violations': violations})
        if outcome.status not in {'OK', 'ACCEPTED'} or outcome.code != 'OK':
            reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')

    def compile_contract(self, brief_id: str, bible_pair: Mapping[str, Any], context_bundle: Mapping[str, Any]) -> Result:
        try:
            check_id(brief_id)
            if not isinstance(bible_pair, Mapping) or not isinstance(bible_pair.get('pair_digest'), str) or len(bible_pair['pair_digest']) != 64:
                reject('INVALID_INPUT')
            def query(tx):
                brief = read_one(tx, 'SELECT * FROM v1_briefs WHERE brief_id=?', (brief_id,))
                if not brief: reject('NOT_FOUND', 'NOT_FOUND')
                project = read_one(tx, 'SELECT * FROM v1_projects WHERE project_id=?', (brief['project_id'],))
                if not project: reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
                constitution = read_one(tx, 'SELECT payload_json FROM v1_project_constitutions WHERE project_id=? AND epoch=1', (brief['project_id'],))
                if not constitution: reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
                return brief, project, json.loads(constitution['payload_json'])
            brief, project, spec = read(self.state, query)
            if project['state'] != 'ACTIVE': reject('SCOPE_EXCEEDED')
            if text_digest(brief['original_text']) != brief['original_digest']: reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
            self._verified_context(brief['project_id'], brief_id, brief['feature_id'], bible_pair['pair_digest'], context_bundle)
            pair_digest, clauses = self._active_bundle(brief['project_id'], brief['feature_id'], bible_pair, context_bundle)
            if not isinstance(spec.get('repository_ref'), str) or not spec['repository_ref']:
                reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
            deliverables, acceptance = self._extract(brief['original_text'], clauses)
            self._conflicts(brief['project_id'], brief['feature_id'], brief_id, brief['original_text'], deliverables, clauses, pair_digest)
            caps = sorted({{'QUEST_GRAPH': 'quest-author', 'CODE': 'game-programmer', 'SCENE': 'game-programmer',
                            'DIALOGUE': 'narrative-designer', 'AUDIO': 'audio-designer', 'ASSET': 'asset-artist',
                            'ANIMATION': 'animator', 'TEST': 'qa-analyst', 'NARRATIVE': 'narrative-designer'}.get(d['kind'], 'producer')
                           for d in deliverables})
            base = {'project_id': brief['project_id'], 'feature_id': brief['feature_id'], 'brief_id': brief_id,
                    'original_brief_digest': brief['original_digest'], 'bible_pair_digest': pair_digest,
                    'revision': 1, 'scope_digest': sha({'project_id': brief['project_id'], 'feature_id': brief['feature_id'],
                                                       'repository_ref': spec['repository_ref'], 'pair_digest': pair_digest}),
                    'deliverables': deliverables, 'acceptance_ids': acceptance, 'budget_limit_units': 0,
                    'required_capabilities': caps, 'unresolved_boundary_refs': [],
                    'repository_ref': spec['repository_ref'], 'feature_branch': f"features/{brief['feature_id']}"}
            base['contract_digest'] = sha(base)
            validate_record('FeatureContract', base)
            return self._store_new(base, bible_pair)
        except Reject as e:
            return e.as_result()

    def _store_new(self, base: dict[str, Any], bible_pair: Mapping[str, Any]) -> Result:
        def transaction(tx):
            self._check_active_in_transaction(tx, bible_pair)
            project = read_one(tx, 'SELECT state FROM v1_projects WHERE project_id=?', (base['project_id'],))
            if not project or project['state'] != 'ACTIVE': reject('SCOPE_EXCEEDED')
            row = read_one(tx, 'SELECT * FROM v1_briefs WHERE brief_id=?', (base['brief_id'],))
            if not row or row['project_id'] != base['project_id'] or row['feature_id'] != base['feature_id']:
                reject('WRONG_FEATURE')
            if row['original_digest'] != base['original_brief_digest']: reject('EVIDENCE_INVALID')
            existing = read_one(tx, 'SELECT payload_json FROM v1_feature_contracts WHERE feature_id=? AND revision=1', (base['feature_id'],))
            if existing:
                if json.loads(existing['payload_json']) == base:
                    return result('OK', base)
                reject('REVISION_CONFLICT', 'CONFLICT')
            tx.execute('INSERT INTO v1_feature_contracts(project_id,feature_id,revision,brief_id,original_brief_digest,bible_pair_digest,contract_digest,scope_digest,deliverables_digest,acceptance_digest,budget_limit_units,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (base['project_id'], base['feature_id'], base['revision'], base['brief_id'], base['original_brief_digest'],
                        base['bible_pair_digest'], base['contract_digest'], base['scope_digest'], sha(base['deliverables']),
                        sha(base['acceptance_ids']), base['budget_limit_units'], canonical(base)))
            return result('OK', base)
        return write(self.state, transaction)

    def get_contract(self, feature_id: str, revision: int) -> Result:
        try:
            check_id(feature_id)
            if type(revision) is not int or revision < 1: reject('INVALID_INPUT')
            def query(tx):
                row = read_one(tx, 'SELECT * FROM v1_feature_contracts WHERE feature_id=? AND revision=?', (feature_id, revision))
                if not row: reject('NOT_FOUND', 'NOT_FOUND')
                payload = json.loads(row['payload_json'])
                validate_record('FeatureContract', payload)
                if sha({k: v for k, v in payload.items() if k != 'contract_digest'}) != row['contract_digest']:
                    reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
                if any((row['project_id'] != payload['project_id'], row['brief_id'] != payload['brief_id'],
                        row['feature_id'] != payload['feature_id'], row['revision'] != payload['revision'],
                        row['bible_pair_digest'] != payload['bible_pair_digest'],
                        row['deliverables_digest'] != sha(payload['deliverables']), row['acceptance_digest'] != sha(payload['acceptance_ids']))):
                    reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
                return result('OK', payload)
            return read(self.state, query)
        except Reject as e:
            return e.as_result()

    def revise_contract(self, feature_id: str, requested_changes: Mapping[str, Any], original_brief: Mapping[str, Any],
                        bible_pair_digest: str) -> Result:
        try:
            check_id(feature_id); check_digest(bible_pair_digest)
            changes = fields(requested_changes,
                             {'project_id', 'feature_id', 'expected_revision', 'deliverable_id', 'description', 'owner_proof_ref'},
                             {'project_id', 'feature_id', 'expected_revision', 'deliverable_id', 'description', 'owner_proof_ref'})
            if changes['feature_id'] != feature_id: reject('WRONG_FEATURE')
            for field in ('project_id', 'deliverable_id', 'owner_proof_ref'): check_id(changes[field])
            check_text(changes['description'])
            expected = changes['expected_revision']
            if type(expected) is not int or expected < 1: reject('INVALID_INPUT')
            original = validate_record('ProductionBrief', original_brief)
            if original['project_id'] != changes['project_id']: reject('WRONG_PROJECT')
            if original['feature_id'] != feature_id: reject('WRONG_FEATURE')
            if text_digest(original['original_text']) != original['original_digest']: reject('EVIDENCE_INVALID')
            existing = self.get_contract(feature_id, expected)
            if existing.status != 'OK': return existing
            current = dict(existing.record)
            if current['project_id'] != changes['project_id']: reject('WRONG_PROJECT')
            if current['brief_id'] != original['brief_id'] or current['original_brief_digest'] != original['original_digest']:
                reject('WRONG_FEATURE')
            if current['bible_pair_digest'] != bible_pair_digest: reject('STALE_BIBLE')
            if self.identity is None: reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
            subject = sha({'feature_id': feature_id,
                           'requested_changes': {k: v for k, v in changes.items() if k != 'owner_proof_ref'},
                           'brief_digest': original['original_digest'], 'bible_pair_digest': bible_pair_digest})
            proof = self.identity.verify_owner_decision(changes['owner_proof_ref'], 'production.amend', subject, changes['project_id'])
            if not (getattr(proof, 'allowed', False) is True and getattr(proof, 'status', '') == 'ALLOW' and getattr(proof, 'code', '') == 'OK'):
                reject('OWNER_ACTION_REQUIRED')
            if self.bible is None: reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
            active = self.bible.snapshot_active_pair(changes['project_id'], {'project_id': changes['project_id'], 'feature_id': feature_id})
            if getattr(active, 'status', None) != 'OK' or not getattr(active, 'record', None) or active.record.get('pair_digest') != bible_pair_digest:
                reject('STALE_BIBLE')
            # Verify and obtain mandatory clauses once more before revising.
            if self.context_port is None:
                reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
            verified_context = self.context_port.build_bundle(changes['project_id'], original['brief_id'], bible_pair_digest, None)
            if getattr(verified_context, 'status', None) != 'OK' or not isinstance(verified_context.record, Mapping):
                reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
            self._verified_context(changes['project_id'], original['brief_id'], feature_id, bible_pair_digest,
                                   verified_context.record)
            _, clauses = self._active_bundle(changes['project_id'], feature_id, active.record, verified_context.record)
            deliverables = [dict(d) for d in current['deliverables']]
            affected = next((d for d in deliverables if d['deliverable_id'] == changes['deliverable_id']), None)
            if not affected: reject('NOT_FOUND', 'NOT_FOUND')
            affected['description'] = changes['description']
            self._conflicts(changes['project_id'], feature_id, original['brief_id'],
                            changes['description'], [affected], clauses, bible_pair_digest)
            amended = {**current, 'revision': expected + 1, 'deliverables': deliverables}
            amended['contract_digest'] = sha({k: v for k, v in amended.items() if k != 'contract_digest'})
            validate_record('FeatureContract', amended)
            def transaction(tx):
                persisted = read_one(tx, 'SELECT * FROM v1_briefs WHERE brief_id=?', (original['brief_id'],))
                if not persisted or persisted['feature_id'] != feature_id or persisted['project_id'] != changes['project_id']:
                    reject('WRONG_FEATURE')
                if persisted['original_digest'] != original['original_digest']: reject('EVIDENCE_INVALID')
                self._check_active_in_transaction(tx, active.record)
                project = read_one(tx, 'SELECT state FROM v1_projects WHERE project_id=?', (changes['project_id'],))
                if not project or project['state'] != 'ACTIVE': reject('SCOPE_EXCEEDED')
                latest = read_one(tx, 'SELECT revision FROM v1_feature_contracts WHERE feature_id=? ORDER BY revision DESC LIMIT 1', (feature_id,))
                if not latest or latest['revision'] != expected: reject('REVISION_CONFLICT', 'CONFLICT')
                tx.execute('INSERT INTO v1_feature_contracts(project_id,feature_id,revision,brief_id,original_brief_digest,bible_pair_digest,contract_digest,scope_digest,deliverables_digest,acceptance_digest,budget_limit_units,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (amended['project_id'], feature_id, expected + 1, amended['brief_id'], amended['original_brief_digest'],
                            bible_pair_digest, amended['contract_digest'], amended['scope_digest'], sha(deliverables),
                            sha(amended['acceptance_ids']), amended['budget_limit_units'], canonical(amended)))
                return result('OK', amended, evidence_refs=(changes['owner_proof_ref'],))
            return write(self.state, transaction)
        except Reject as e:
            return e.as_result()
