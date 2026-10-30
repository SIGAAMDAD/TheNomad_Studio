"""Project-scoped Bible, Canon and human-owner authority domain.

All mutations are made inside WG-C's injected atomic scoped transaction. This
module never opens a database connection, talks to a model, or trusts GitHub
worker messages as identity proof.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Any, Callable

from .model import (
    Bible, BusinessEvent, Clause, Decision, Edition, Effect, Fact, GovernanceError, Intent,
    Manifest, Policy, Principal, ProjectState, Rule, Snapshot,
)
from .ports import GovernanceTransactions, ImpactCatalog, SubjectCatalog, TrustedIdentity, VerifiedBlobSource


ALLOWED_KINDS = {'design', 'technical'}
SUBJECT_TYPES = {'project', 'bible', 'feature', 'task', 'cabal', 'artifact', 'release'}
DISPOSITIONS = {'forbidden', 'delegated', 'owner_required'}
FACT_STATES = {'draft', 'verified', 'superseded', 'disputed'}
CLAUSE_CLASSES = {'requirement', 'guideline', 'open_area'}
CLAUSE_STATES = {'proposed', 'verified', 'disputed', 'rejected'}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _hex(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in '0123456789abcdef' for ch in value):
        raise GovernanceError('invalid', 'expected lowercase SHA256 digest')
    return value


def _id(prefix: str, *parts: Any) -> str:
    return prefix + '_' + digest(parts)[:32]


def _text(value: Any, name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GovernanceError('invalid', f'invalid {name}')
    return value


def _optional_edition(value: Any) -> str | None:
    if value is None or value == '':
        return None
    return _text(value, 'edition')


def parse_manifest(data: bytes) -> Manifest:
    """Parse a versioned immutable manifest; reject unsupported or ambiguous data.

    Unreviewed extraction is always 'proposed'; no text is inferred into facts.
    """
    if len(data) > 8 * 1024 * 1024:
        raise GovernanceError('invalid', 'manifest exceeds size bound')
    try:
        root = json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))
    except (ValueError, UnicodeError) as exc:
        raise GovernanceError('invalid', 'manifest is not valid UTF-8 JSON') from exc
    if not isinstance(root, dict) or root.get('format') != 'nomad.bible.manifest.v1':
        raise GovernanceError('invalid', 'unsupported manifest format')
    raw_clauses = root.get('clauses')
    raw_facts = root.get('facts')
    if not isinstance(raw_clauses, list) or not isinstance(raw_facts, list) or len(raw_clauses) > 20000 or len(raw_facts) > 20000:
        raise GovernanceError('invalid', 'invalid manifest entries')
    clauses: list[Clause] = []
    keys: set[str] = set()
    for raw in raw_clauses:
        if not isinstance(raw, dict):
            raise GovernanceError('invalid', 'invalid clause')
        key = _text(raw.get('clause_key'), 'clause key')
        if key in keys:
            raise GovernanceError('conflict', 'duplicate clause key')
        keys.add(key)
        classification = raw.get('classification')
        status = raw.get('status', 'proposed')
        if classification not in CLAUSE_CLASSES or status not in CLAUSE_STATES:
            raise GovernanceError('invalid', 'invalid clause classification or review state')
        text = _text(raw.get('text'), 'clause text', maximum=100000)
        scope = raw.get('scope')
        source = raw.get('source')
        if not isinstance(source, dict) or not isinstance(scope, (dict, list, str)):
            raise GovernanceError('invalid', 'clause needs explicit scope and source')
        for field in ('document_id', 'revision', 'start', 'end'):
            if field not in source:
                raise GovernanceError('invalid', f'clause source missing {field}')
        if not isinstance(source['start'], int) or not isinstance(source['end'], int) or source['start'] < 0 or source['end'] <= source['start']:
            raise GovernanceError('invalid', 'invalid source span')
        tasks = raw.get('impacted_task_ids', [])
        artifacts = raw.get('impacted_artifact_ids', [])
        if not isinstance(tasks, list) or not isinstance(artifacts, list) or any(not isinstance(x, str) for x in tasks + artifacts):
            raise GovernanceError('invalid', 'invalid impact references')
        clauses.append(Clause(key, classification, digest(text), digest(scope), digest(source), status,
                              text, source, tuple(sorted(set(tasks))), tuple(sorted(set(artifacts)))))
    facts: list[Fact] = []
    fact_keys: set[str] = set()
    for raw in raw_facts:
        if not isinstance(raw, dict):
            raise GovernanceError('invalid', 'invalid fact')
        fact_id = _text(raw.get('fact_id'), 'fact id')
        if fact_id in fact_keys:
            raise GovernanceError('conflict', 'duplicate fact id')
        fact_keys.add(fact_id)
        state = raw.get('status', 'draft')
        if state not in FACT_STATES:
            raise GovernanceError('invalid', 'invalid fact review state')
        subject = _text(raw.get('subject_key'), 'fact subject')
        predicate = _text(raw.get('predicate'), 'fact predicate')
        if 'value' not in raw or not isinstance(raw.get('evidence'), dict) or not raw['evidence']:
            raise GovernanceError('invalid', 'fact requires value and source evidence')
        evidence = raw['evidence']
        if not isinstance(evidence.get('source'), str) or not evidence['source']:
            raise GovernanceError('invalid', 'fact requires cited source')
        facts.append(Fact(fact_id, subject, predicate, digest(raw['value']), digest(evidence),
                          state, evidence, raw['value']))
    return Manifest(tuple(sorted(clauses, key=lambda x: x.clause_key)), tuple(sorted(facts, key=lambda x: x.fact_id)))


class Governance:
    def __init__(self, transactions: GovernanceTransactions, identity: TrustedIdentity,
                 blobs: VerifiedBlobSource, subjects: SubjectCatalog,
                 impacts: ImpactCatalog) -> None:
        self.transactions = transactions
        self.identity = identity
        self.blobs = blobs
        self.subjects = subjects
        self.impacts = impacts

    def _actor(self, actor: dict[str, Any], project_id: str) -> Principal:
        principal = self.identity.authenticate(actor)
        # allowed_project_ids is a narrowing constraint, never a grant source.
        if project_id not in actor.get('allowed_project_ids', []):
            raise GovernanceError('forbidden', 'project is outside authenticated request scope')
        return principal

    @staticmethod
    def _require(s: ProjectState, principal: Principal, *, owner: bool = False) -> None:
        if owner:
            if principal.kind != 'human' or (principal.principal_id, 'owner') not in s.grants:
                raise GovernanceError('requires_owner', 'verified human project owner required')
        elif not any(p == principal.principal_id for p, _ in s.grants):
            raise GovernanceError('forbidden', 'project access denied')

    @staticmethod
    def _request(s: ProjectState, operation: str, request: dict[str, Any]) -> tuple[str, Any | None]:
        request_id = _text(request.get('request_id'), 'request id')
        body_hash = digest(request)
        old = s.requests.get((operation, request_id))
        if old is not None:
            if old[0] != body_hash:
                raise GovernanceError('conflict', 'idempotency key reused with altered request body')
            return body_hash, old[1]
        return body_hash, None

    @staticmethod
    def _event(s: ProjectState, principal: Principal, event_type: str,
               subject_id: str, payload: Any) -> None:
        # WG-C maps these atomic business events into audit_events/outbox.
        s.events.append(BusinessEvent(event_type, subject_id, digest(payload), principal.principal_id))

    @staticmethod
    def _save(s: ProjectState, operation: str, request_id: str, body_hash: str, response: Any) -> Any:
        s.requests[(operation, request_id)] = (body_hash, response)
        return response

    def listBibles(self, actor: dict[str, Any], project_id: str) -> dict[str, Any]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            result = []
            for b in sorted(s.bibles.values(), key=lambda x: x.bible_id):
                pair = s.active.get(b.kind)
                result.append({'bible_id': b.bible_id, 'kind': b.kind,
                               'active_edition_id': pair[1] if pair and pair[0] == b.bible_id else ''})
            return {'bibles': result}

    def createBible(self, actor: dict[str, Any], project_id: str, request: dict[str, Any]) -> dict[str, str]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            h, replay = self._request(s, 'createBible', request)
            if replay is not None:
                return replay
            kind = request.get('kind')
            if kind not in ALLOWED_KINDS:
                raise GovernanceError('invalid', 'Bible kind must be design or technical')
            title = _text(request.get('title'), 'Bible title')
            bible_id = _id('bible', project_id, request['request_id'])
            if bible_id in s.bibles:
                raise GovernanceError('conflict', 'Bible already exists')
            s.bibles[bible_id] = Bible(bible_id, kind, title)
            self._event(s, principal, 'bible.created', bible_id, {'kind': kind, 'title': title})
            return self._save(s, 'createBible', request['request_id'], h, {'bible_id': bible_id, 'kind': kind})

    def stageBible(self, actor: dict[str, Any], project_id: str, bible_id: str,
                   request: dict[str, Any]) -> dict[str, str]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            h, replay = self._request(s, 'stageBible', request)
            if replay is not None:
                return replay
            if bible_id not in s.bibles:
                raise GovernanceError('missing', 'Bible not found for project')
            expected = _optional_edition(request.get('expected_edition_id'))
            pair = s.active.get(s.bibles[bible_id].kind)
            active = pair[1] if pair is not None else None
            if active != expected:
                raise GovernanceError('stale_revision', 'active Bible edition changed')
            content_digest = _hex(request.get('content_digest'))
            manifest_digest = _hex(request.get('manifest_digest'))
            content_bytes = self.blobs.read_verified(project_id, content_digest, 'bible-content')
            manifest_bytes = self.blobs.read_verified(project_id, manifest_digest, 'bible-manifest')
            if hashlib.sha256(content_bytes).hexdigest() != content_digest or hashlib.sha256(manifest_bytes).hexdigest() != manifest_digest:
                raise GovernanceError('conflict', 'blob verification failed')
            try:
                content_bytes.decode('utf-8', errors='strict')
            except UnicodeError as exc:
                raise GovernanceError('invalid', 'Bible content must be UTF-8') from exc
            parse_manifest(manifest_bytes)
            s.sequence += 1
            intent_id = _id('intent', project_id, bible_id, request['request_id'])
            s.intents[intent_id] = Intent(intent_id, bible_id, request['request_id'],
                                          content_digest, manifest_digest, expected, 'verified', s.sequence)
            self._event(s, principal, 'bible.staged_verified', intent_id, {'content_digest': content_digest, 'manifest_digest': manifest_digest})
            return self._save(s, 'stageBible', request['request_id'], h,
                              {'intent_id': intent_id, 'state': 'verified'})

    @staticmethod
    def _latest_intent(s: ProjectState, bible_id: str) -> Intent:
        choices = [x for x in s.intents.values() if x.bible_id == bible_id and x.state == 'verified']
        if not choices:
            raise GovernanceError('missing', 'no verified staged edition')
        return max(choices, key=lambda x: x.sequence)

    def _load_staged_manifest(self, project_id: str, intent: Intent) -> Manifest:
        return parse_manifest(self.blobs.read_verified(project_id, intent.manifest_digest, 'bible-manifest'))

    def diffBible(self, actor: dict[str, Any], project_id: str, bible_id: str) -> dict[str, Any]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            if bible_id not in s.bibles:
                raise GovernanceError('missing', 'Bible not found')
            new = self._load_staged_manifest(project_id, self._latest_intent(s, bible_id))
            pair = s.active.get(s.bibles[bible_id].kind)
            old = s.editions[pair[1]].manifest if pair else Manifest((), ())
            old_keys = {c.clause_key: c for c in old.clauses}
            new_keys = {c.clause_key: c for c in new.clauses}
            changed = {k for k in old_keys.keys() | new_keys.keys() if old_keys.get(k) != new_keys.get(k)}
            impacted = set(self.impacts.referenced_tasks(project_id, changed))
            for key in changed:
                for source in (old_keys, new_keys):
                    if key in source:
                        impacted.update(source[key].impacted_task_ids)
            return {'changed_clause_ids': sorted(changed), 'impacted_task_ids': sorted(impacted)}

    @staticmethod
    def publication_effect(project_id: str, bible_id: str, intent: Intent) -> str:
        return digest({'operation': 'bible.publish', 'project_id': project_id, 'bible_id': bible_id,
                       'intent_id': intent.intent_id, 'expected_edition_id': intent.expected_edition_id,
                       'content_digest': intent.content_digest, 'manifest_digest': intent.manifest_digest})

    def prepare_publication_effect(self, actor: dict[str, Any], project_id: str, bible_id: str,
                                   intent_id: str) -> str:
        """Produce exact proposal digest without mutation, for owner inspection."""
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            intent = s.intents.get(intent_id)
            if intent is None or intent.bible_id != bible_id:
                raise GovernanceError('missing', 'publication intent not found')
            return self.publication_effect(project_id, bible_id, intent)

    def _make_snapshot(self, s: ProjectState) -> str:
        if 'design' not in s.active or 'technical' not in s.active:
            s.current_snapshot_id = None
            return ''
        db, de = s.active['design']
        tb, te = s.active['technical']
        policy = s.policy.policy_digest if s.policy else digest({'policy': 'unconfigured'})
        edition_pairs = [(db, de), (tb, te)]
        manifest_digest = digest({'project_id': s.project_id, 'editions': edition_pairs, 'policy': policy})
        snapshot_id = _id('canon', s.project_id, manifest_digest)
        s.snapshots.setdefault(snapshot_id, Snapshot(snapshot_id, db, de, tb, te, policy, manifest_digest))
        s.current_snapshot_id = snapshot_id
        return snapshot_id

    def publishBible(self, actor: dict[str, Any], project_id: str, bible_id: str,
                     request: dict[str, Any]) -> dict[str, str]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            h, replay = self._request(s, 'publishBible', request)
            if replay is not None:
                return replay
            b = s.bibles.get(bible_id)
            decision_id = _text(request.get('decision_id'), 'decision id')
            if decision_id in s.consumed_decisions:
                raise GovernanceError('conflict', 'owner decision already consumed')
            intent = s.intents.get(request.get('intent_id'))
            if b is None or intent is None or intent.bible_id != bible_id or intent.state != 'verified':
                raise GovernanceError('missing', 'verified publication intent not found')
            expected = _optional_edition(request.get('expected_edition_id'))
            pair = s.active.get(b.kind)
            active = pair[1] if pair else None
            if intent.expected_edition_id != active or expected != intent.expected_edition_id:
                raise GovernanceError('stale_revision', 'Bible base edition mismatch')
            effect_digest = _hex(request.get('effect_digest'))
            if self.publication_effect(project_id, bible_id, intent) != effect_digest:
                raise GovernanceError('conflict', 'publication effect digest mismatch')
            decision_id = _text(request.get('decision_id'), 'decision id')
            decision = s.decisions.get(decision_id)
            if decision is None or decision.state != 'approved' or (decision.subject_type, decision.subject_id, decision.action, decision.effect_digest) != ('bible', bible_id, 'bible.publish', effect_digest):
                raise GovernanceError('requires_owner', 'exact approved owner decision required')
            if decision.snapshot_id != s.current_snapshot_id:
                raise GovernanceError('stale_snapshot', 'decision approved against different Canon')
            if decision_id in s.consumed_decisions:
                raise GovernanceError('conflict', 'owner decision already consumed')
            # Re-read every byte during the commit boundary; do not trust prior stage alone.
            self.blobs.read_verified(project_id, intent.content_digest, 'bible-content')
            manifest = self._load_staged_manifest(project_id, intent)
            edition_id = _id('edition', project_id, bible_id, intent.content_digest, intent.manifest_digest)
            if edition_id in s.editions:
                raise GovernanceError('conflict', 'immutable Bible edition exists')
            effect_id = _id('effect', project_id, request['request_id'], effect_digest)
            s.effects[effect_id] = Effect(effect_id, 'bible', bible_id, 'bible.publish', effect_digest,
                                          decision.snapshot_id, decision_id, request['request_id'])
            s.consumed_decisions[decision_id] = effect_id
            s.editions[edition_id] = Edition(edition_id, bible_id, b.kind, intent.content_digest,
                                             intent.manifest_digest, decision_id, effect_digest, manifest)
            s.active[b.kind] = (bible_id, edition_id)
            s.intents[intent.intent_id] = replace(intent, state='committed')
            snapshot_id = self._make_snapshot(s)
            self._event(s, principal, 'bible.published', edition_id, {'snapshot_id': snapshot_id, 'effect_digest': effect_digest})
            return self._save(s, 'publishBible', request['request_id'], h,
                              {'edition_id': edition_id, 'snapshot_id': snapshot_id})

    def getCanon(self, actor: dict[str, Any], project_id: str) -> dict[str, str]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            if not s.current_snapshot_id or 'design' not in s.active or 'technical' not in s.active:
                raise GovernanceError('missing', 'complete published Canon does not exist')
            snap = s.snapshots[s.current_snapshot_id]
            return {'snapshot_id': snap.snapshot_id, 'design_edition_id': snap.design_edition_id,
                    'technical_edition_id': snap.technical_edition_id}

    def compile_context(self, actor: dict[str, Any], project_id: str, snapshot_id: str,
                        clause_keys: set[str] | None = None, max_entries: int = 500) -> dict[str, Any]:
        """Pinned context only; deterministic ordering, explicit omission manifest."""
        principal = self._actor(actor, project_id)
        if not 1 <= max_entries <= 5000:
            raise GovernanceError('invalid', 'context entry bound')
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            snapshot = s.snapshots.get(snapshot_id)
            if snapshot is None:
                raise GovernanceError('missing', 'snapshot not in project')
            entries: list[dict[str, Any]] = []
            for bible_id, edition_id in ((snapshot.design_bible_id, snapshot.design_edition_id),
                                         (snapshot.technical_bible_id, snapshot.technical_edition_id)):
                ed = s.editions[edition_id]
                for clause in ed.manifest.clauses:
                    if clause.status != 'verified' or (clause_keys is not None and clause.clause_key not in clause_keys):
                        continue
                    entries.append({'kind': 'clause', 'bible_id': bible_id, 'edition_id': edition_id,
                                    'key': clause.clause_key, 'classification': clause.classification,
                                    'text': clause.text, 'text_digest': clause.text_digest,
                                    'source': clause.source, 'provenance_digest': clause.provenance_digest})
                for fact in ed.manifest.facts:
                    if fact.status != 'verified':
                        continue
                    entries.append({'kind': 'fact', 'bible_id': bible_id, 'edition_id': edition_id,
                                    'key': fact.fact_id, 'subject_key': fact.subject_key,
                                    'predicate': fact.predicate, 'value': fact.value,
                                    'value_digest': fact.value_digest, 'evidence': fact.evidence,
                                    'evidence_digest': fact.evidence_digest})
            entries.sort(key=lambda x: (x['kind'], x['bible_id'], x['key']))
            kept = entries[:max_entries]
            return {'snapshot_id': snapshot_id, 'manifest_digest': snapshot.manifest_digest,
                    'policy_digest': snapshot.policy_digest,
                    'entries': kept,
                    'omissions': [{'key': x['key'], 'kind': x['kind'], 'bible_id': x['bible_id']}
                                  for x in entries[max_entries:]],
                    'context_digest': digest(kept)}

    def _subject_exists(self, s: ProjectState, subject_type: str, subject_id: str) -> bool:
        if subject_type == 'project':
            return s.project_id == subject_id
        if subject_type == 'bible':
            return subject_id in s.bibles
        return self.subjects.exists(s.project_id, subject_type, subject_id)

    def requestDecision(self, actor: dict[str, Any], project_id: str,
                        request: dict[str, Any]) -> dict[str, str]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            h, replay = self._request(s, 'requestDecision', request)
            if replay is not None:
                return replay
            subject_type = request.get('subject_type')
            subject_id = _text(request.get('subject_id'), 'subject id')
            action = _text(request.get('action'), 'action')
            effect_digest = _hex(request.get('effect_digest'))
            if subject_type not in SUBJECT_TYPES or not self._subject_exists(s, subject_type, subject_id):
                raise GovernanceError('missing', 'subject not found within project')
            snapshot_id = request.get('snapshot_id')
            if snapshot_id is not None and snapshot_id not in s.snapshots:
                raise GovernanceError('stale_snapshot', 'unknown project snapshot')
            # Exact version pinned to proposal; approval cannot switch scope/digest.
            proposal_id = _id('decision', project_id, request['request_id'])
            s.proposals[proposal_id] = Decision(proposal_id, subject_type, subject_id, action,
                                                 effect_digest, snapshot_id, principal.principal_id)
            self._event(s, principal, 'owner_decision.requested', proposal_id, {'effect_digest': effect_digest})
            return self._save(s, 'requestDecision', request['request_id'], h,
                              {'decision_id': proposal_id, 'state': 'pending'})

    def resolveDecision(self, actor: dict[str, Any], project_id: str, decision_id: str,
                        request: dict[str, Any]) -> dict[str, str]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal, owner=True)
            h, replay = self._request(s, 'resolveDecision', request)
            if replay is not None:
                return replay
            decision = s.proposals.get(decision_id)
            if decision is None:
                raise GovernanceError('missing', 'decision proposal does not exist')
            if request.get('expected_revision') != decision.revision:
                raise GovernanceError('stale_revision', 'decision revision mismatch')
            if request.get('expected_effect_digest') != decision.effect_digest:
                raise GovernanceError('conflict', 'decision effect digest differs')
            choice = request.get('decision')
            if choice not in {'approve', 'reject', 'revoke'}:
                raise GovernanceError('invalid', 'invalid resolution')
            if choice == 'revoke':
                previous = s.decisions.get(decision_id)
                if previous is None or previous.state != 'approved' or decision_id in s.consumed_decisions:
                    raise GovernanceError('conflict', 'decision is not revocable')
                updated = replace(previous, state='revoked', revision=decision.revision + 1)
            else:
                if decision.state != 'pending' or decision_id in s.decisions:
                    raise GovernanceError('conflict', 'decision already resolved')
                if decision.snapshot_id is not None and decision.snapshot_id != s.current_snapshot_id:
                    raise GovernanceError('stale_snapshot', 'proposal snapshot became stale')
                updated = replace(decision, state='approved' if choice == 'approve' else 'rejected',
                                  revision=decision.revision + 1,
                                  verified_owner_principal_id=principal.principal_id,
                                  proof_digest=digest({'issuer': principal.issuer,
                                                       'proof_ref': actor['trusted_proof_ref'],
                                                       'decision_id': decision_id,
                                                       'effect_digest': decision.effect_digest}))
            s.decisions[decision_id] = updated
            # SQL decision_proposals permits approved/rejected, not revoked.
            s.proposals[decision_id] = (replace(decision, revision=updated.revision)
                                        if choice == 'revoke' else updated)
            self._event(s, principal, 'owner_decision.resolved', decision_id, {'state': updated.state, 'effect_digest': updated.effect_digest})
            return self._save(s, 'resolveDecision', request['request_id'], h,
                              {'decision_id': decision_id, 'state': updated.state})

    def listDecisions(self, actor: dict[str, Any], project_id: str) -> dict[str, Any]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            return {'decisions': [{'decision_id': d.decision_id, 'subject_type': d.subject_type,
                                   'subject_id': d.subject_id, 'action': d.action,
                                   'effect_digest': d.effect_digest,
                                   'state': s.decisions[d.decision_id].state if d.decision_id in s.decisions else d.state}
                                  for d in sorted(s.proposals.values(), key=lambda x: x.decision_id)]}

    def getProjectPolicy(self, actor: dict[str, Any], project_id: str) -> dict[str, Any]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            if s.policy is None:
                raise GovernanceError('missing', 'project owner has not selected a policy')
            return {'policy_id': s.policy.policy_id, 'revision': s.policy.revision,
                    'policy_digest': s.policy.policy_digest}

    def setProjectPolicy(self, actor: dict[str, Any], project_id: str,
                         request: dict[str, Any]) -> dict[str, Any]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal, owner=True)
            h, replay = self._request(s, 'setProjectPolicy', request)
            if replay is not None:
                return replay
            revision = s.policy.revision if s.policy else 0
            if type(request.get('expected_revision')) is not int or request['expected_revision'] != revision:
                raise GovernanceError('stale_revision', 'project policy revision conflict')
            raw = request.get('rules')
            if not isinstance(raw, list) or len(raw) > 10000:
                raise GovernanceError('invalid', 'invalid policy rule list')
            rules: list[Rule] = []
            seen: set[str] = set()
            for entry in raw:
                if not isinstance(entry, dict):
                    raise GovernanceError('invalid', 'invalid rule')
                rule_id = _text(entry.get('rule_id'), 'rule id')
                if rule_id in seen:
                    raise GovernanceError('conflict', 'duplicate rule')
                seen.add(rule_id)
                subject_type = _text(entry.get('subject_type'), 'rule subject type')
                action = _text(entry.get('action'), 'rule action')
                scope_digest = _hex(entry.get('scope_digest'))
                disposition = entry.get('disposition')
                if disposition not in DISPOSITIONS:
                    raise GovernanceError('invalid', 'invalid policy disposition')
                rules.append(Rule(rule_id, subject_type, action, scope_digest, disposition))
            rules.sort(key=lambda x: x.rule_id)
            computed_digest = digest([vars(r) for r in rules])
            if request.get('policy_digest') != computed_digest:
                raise GovernanceError('conflict', 'policy digest differs from exact rules')
            next_revision = revision + 1
            policy_id = _id('policy', project_id, next_revision, computed_digest)
            s.policy = Policy(policy_id, next_revision, computed_digest, tuple(rules))
            self._make_snapshot(s)
            self._event(s, principal, 'policy.updated', policy_id, {'revision': next_revision, 'digest': computed_digest})
            return self._save(s, 'setProjectPolicy', request['request_id'], h,
                              {'policy_id': policy_id, 'revision': next_revision, 'policy_digest': computed_digest})

    def evaluate_policy(self, actor: dict[str, Any], project_id: str, *, subject_type: str,
                        action: str, scope_digest: str) -> str:
        """No implicit grant: unspecified action remains owner_required."""
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            if s.policy is None:
                return 'owner_required'
            matches = [r.disposition for r in s.policy.rules if
                       r.subject_type == subject_type and r.action == action and r.scope_digest == scope_digest]
            if not matches:
                return 'owner_required'
            if 'forbidden' in matches:
                return 'forbidden'
            if 'owner_required' in matches:
                return 'owner_required'
            return 'delegated'

    def authorize_effect(self, actor: dict[str, Any], project_id: str, *, effect_id: str,
                         subject_type: str, subject_id: str, action: str, effect_digest: str,
                         snapshot_id: str | None, decision_id: str | None,
                         request_id: str, scope_digest: str) -> Effect:
        """Validate exact action and consume a human decision before protected dispatch.

        Call only from a trusted service transaction combined with WG-C's effect
        intent write. Never execute an external effect from this method.
        """
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            _hex(effect_digest)
            _hex(scope_digest)
            if not self._subject_exists(s, subject_type, subject_id):
                raise GovernanceError('missing', 'effect subject not in project')
            if snapshot_id is not None and snapshot_id != s.current_snapshot_id:
                raise GovernanceError('stale_snapshot', 'effect snapshot is stale')
            if effect_id in s.effects:
                existing = s.effects[effect_id]
                if (existing.subject_type, existing.subject_id, existing.action, existing.effect_digest,
                    existing.snapshot_id, existing.decision_id, existing.request_id) == (
                    subject_type, subject_id, action, effect_digest, snapshot_id, decision_id, request_id):
                    return existing
                raise GovernanceError('conflict', 'effect id reused for different effect')
            if any(e.request_id == request_id for e in s.effects.values()):
                raise GovernanceError('conflict', 'effect request id was already used')
            disposition = 'owner_required'
            if s.policy is not None:
                matches = [r.disposition for r in s.policy.rules if r.subject_type == subject_type
                           and r.action == action and r.scope_digest == scope_digest]
                if 'forbidden' in matches:
                    disposition = 'forbidden'
                elif 'owner_required' in matches:
                    disposition = 'owner_required'
                elif 'delegated' in matches:
                    disposition = 'delegated'
            if disposition == 'forbidden':
                raise GovernanceError('forbidden', 'project policy forbids effect')
            if disposition == 'delegated' and (principal.principal_id, 'delegate:' + action) in s.grants:
                if decision_id is not None:
                    raise GovernanceError('invalid', 'delegated effect cannot consume owner approval')
            else:
                decision = s.decisions.get(decision_id or '')
                if decision is None or decision.state != 'approved':
                    raise GovernanceError('requires_owner', 'exact human approval required')
                if (decision.subject_type, decision.subject_id, decision.action,
                    decision.effect_digest, decision.snapshot_id) != (
                    subject_type, subject_id, action, effect_digest, snapshot_id):
                    raise GovernanceError('requires_owner', 'approval differs from effect identity')
                if decision_id in s.consumed_decisions:
                    raise GovernanceError('conflict', 'approval already consumed')
                s.consumed_decisions[decision_id] = effect_id
            effect = Effect(effect_id, subject_type, subject_id, action, effect_digest,
                            snapshot_id, decision_id, request_id)
            s.effects[effect_id] = effect
            self._event(s, principal, 'effect.authorized', effect_id, {'action': action, 'effect_digest': effect_digest})
            return effect

    def listProjectGrants(self, actor: dict[str, Any], project_id: str) -> dict[str, Any]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal)
            if (principal.principal_id, 'owner') in s.grants:
                grants = s.grants
            else:
                grants = {g for g in s.grants if g[0] == principal.principal_id}
            return {'grants': [{'principal_id': p, 'grant_code': g} for p, g in sorted(grants)]}

    def grantProjectAccess(self, actor: dict[str, Any], project_id: str,
                           request: dict[str, Any]) -> dict[str, str]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal, owner=True)
            h, replay = self._request(s, 'grantProjectAccess', request)
            if replay is not None:
                return replay
            target = _text(request.get('principal_id'), 'grant principal')
            grant = _text(request.get('grant_code'), 'grant code')
            # Principal existence and type must be proven by J's durable principal directory.
            if not self.subjects.exists(project_id, 'principal', target):
                raise GovernanceError('missing', 'target principal does not exist')
            s.grants.add((target, grant))
            self._event(s, principal, 'project_grant.created', target, {'grant_code': grant})
            return self._save(s, 'grantProjectAccess', request['request_id'], h,
                              {'principal_id': target, 'grant_code': grant})

    def revokeProjectAccess(self, actor: dict[str, Any], project_id: str,
                            request: dict[str, Any]) -> dict[str, str]:
        principal = self._actor(actor, project_id)
        with self.transactions.project(project_id) as s:
            self._require(s, principal, owner=True)
            h, replay = self._request(s, 'revokeProjectAccess', request)
            if replay is not None:
                return replay
            target = _text(request.get('principal_id'), 'grant principal')
            grant = _text(request.get('grant_code'), 'grant code')
            if (target, grant) not in s.grants:
                raise GovernanceError('missing', 'project grant not found')
            s.grants.remove((target, grant))
            self._event(s, principal, 'project_grant.revoked', target, {'grant_code': grant})
            return self._save(s, 'revokeProjectAccess', request['request_id'], h,
                              {'principal_id': target, 'grant_code': grant})
