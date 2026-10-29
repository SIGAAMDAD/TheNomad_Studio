"""WG-K artifact registrar with independent byte readback and QA/publication ports.

No direct provider writes, source pushes, or SQL connections are created here.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from importlib.resources import files
from typing import Any, Callable, Mapping

from nomad_studio.contracts.v1 import (
    CommandEnvelope, Component, DomainEvent, Outcome,
    QueryEnvelope, StateStore, VerifiedIdentity,
)
from .formats import MissingTool, validate
from .models import ArtifactManifest, SchemaError, sha256_json

SCOPES = {'register': 'artifacts:register', 'verify_bytes': 'artifacts:verify', 'validate_format': 'artifacts:verify', 'attach': 'artifacts:attach', 'proof': 'artifacts:publish_intent'}
OPERATIONS = frozenset(SCOPES)
STATES = frozenset({'ACCEPTED', 'DENIED', 'UNKNOWN', 'CONFLICT', 'BLOCKED', 'NOT_RUN'})


def _canon(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _outcome(state: str, code: str, **payload: object) -> Outcome:
    return Outcome(state, code, payload=payload)


def _error(err: SchemaError) -> Outcome:
    code = err.code
    state = 'BLOCKED' if code in ('NOT_CONFIGURED', 'BLOCKED_MISSING_TOOL') else 'DENIED'
    if code in ('BLOCKED_MISSING_TOOL', 'TOOL_FAILED'): state = 'NOT_RUN'
    return _outcome(state, code, reason=str(err))


def _strict_fields(payload: object, fields: frozenset[str], required: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(payload, dict) or payload.keys() - fields or not required <= payload.keys():
        raise SchemaError('INVALID_SCHEMA', 'unexpected or missing command fields')
    return payload


def _small_id(value: object, name: str, maxlen: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maxlen or value != value.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise SchemaError('INVALID_SCHEMA', f'{name} invalid')
    return value


class ArtifactsComponent:
    name = 'artifacts'

    def __init__(self, deps: Mapping[str, object]) -> None:
        self.store: StateStore | None = deps.get('store')  # type: ignore[assignment]
        self.readback = deps.get('readback')
        self.authorize = deps.get('authorize')
        self.review: Component | None = deps.get('review')  # type: ignore[assignment]
        self.publication: Component | None = deps.get('publication')  # type: ignore[assignment]
        folders = deps.get('folder_registry', {})
        self.folders: Mapping[str, str] = folders if isinstance(folders, Mapping) else {}
        tools = deps.get('format_tools', {})
        self.tools = tools if isinstance(tools, Mapping) else {}
        self.now_us: Callable[[], int] = deps.get('now_us', lambda: time.time_ns() // 1000)  # type: ignore[assignment]
        self.max_bytes = deps.get('max_bytes', 256 * 1024 * 1024)
        if type(self.max_bytes) is not int or self.max_bytes < 1:
            raise ValueError('max_bytes must be positive integer')

    def _guard(self, identity: VerifiedIdentity, operation: str, resource: str = '') -> Outcome | None:
        if not isinstance(identity, VerifiedIdentity) or not identity.principal_id or not identity.credential_id or not identity.issuer_id or not identity.authenticated_via:
            return _outcome('DENIED', 'IDENTITY_UNVERIFIED')
        if identity.expires_at_us <= self.now_us() or identity.current_authority_epoch < 0:
            return _outcome('DENIED', 'EXPIRED_GRANT')
        required = SCOPES.get(operation.split('.', 1)[-1], 'artifacts:read')
        if required not in identity.scopes:
            return _outcome('DENIED', 'INVALID_SCOPE')
        if not callable(self.authorize):
            return _outcome('BLOCKED', 'NOT_CONFIGURED', reason='trusted current-epoch authorizer absent')
        try:
            decision = self.authorize(identity, required, resource)
        except Exception:
            return _outcome('UNKNOWN', 'PROVIDER_UNKNOWN', reason='authorization port error')
        if decision is not True:
            return _outcome('DENIED', 'INVALID_SCOPE')
        if self.store is None:
            return _outcome('BLOCKED', 'NOT_CONFIGURED', reason='StateStore absent')
        return None

    def _event(self, tx: object, identity: VerifiedIdentity, cmd: CommandEnvelope, kind: str, payload: Mapping[str, object]) -> str:
        ev = DomainEvent(
            event_id=str(uuid.uuid4()), domain='artifacts', kind=kind,
            actor_principal_id=identity.principal_id, issued_at_us=self.now_us(),
            correlation_id=cmd.request_id, job_id=cmd.job_id, shift_id=cmd.shift_id,
            assignment_epoch=cmd.assignment_epoch, payload_sha256=sha256_json(payload),
            payload={'artifact_id': payload.get('artifact_id'), 'subject_sha256': payload.get('subject_sha256')},
        )
        return tx.append_event(ev)  # type: ignore[attr-defined]

    def _read_row(self, artifact_id: str) -> Mapping[str, Any] | None:
        assert self.store is not None
        return self.store.read(lambda r: r.fetch_one('SELECT * FROM artifacts_manifests WHERE artifact_id=?', (artifact_id,)))

    def _get_manifest(self, artifact_id: str) -> tuple[Mapping[str, Any], ArtifactManifest]:
        row = self._read_row(artifact_id)
        if row is None:
            raise SchemaError('INVALID_SCHEMA', 'artifact not registered')
        m = ArtifactManifest.parse(json.loads(row['manifest_json']), self.folders)
        if m.digest() != row['manifest_sha256']:
            raise SchemaError('SOURCE_SHA_MISMATCH', 'stored manifest digest mismatch')
        return row, m

    def _command_digest(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> str:
        return sha256_json({'op': cmd.operation, 'payload': cmd.payload, 'principal': identity.principal_id, 'job_id': cmd.job_id, 'epoch': cmd.assignment_epoch})

    def _prior_request(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome | None:
        assert self.store is not None
        old = self.store.read(lambda r: r.fetch_one('SELECT * FROM artifacts_requests WHERE idempotency_key=?', (cmd.idempotency_key,)))
        if old is None: return None
        if old['input_sha256'] != self._command_digest(cmd, identity) or old['operation'] != cmd.operation:
            return _outcome('CONFLICT', 'IDEMPOTENCY_CONFLICT')
        stored = json.loads(old['outcome_json'])
        return Outcome(stored['state'], stored['code'], stored.get('receipt_id'), stored.get('payload', {}))

    def _mutation(self, cmd: CommandEnvelope, identity: VerifiedIdentity, action: Callable[[object], Outcome]) -> Outcome:
        assert self.store is not None
        command_digest = self._command_digest(cmd, identity)

        def run(tx: object) -> Outcome:
            old = tx.fetch_one('SELECT * FROM artifacts_requests WHERE idempotency_key=?', (cmd.idempotency_key,))  # type: ignore[attr-defined]
            if old:
                if old['input_sha256'] != command_digest or old['operation'] != cmd.operation:
                    return _outcome('CONFLICT', 'IDEMPOTENCY_CONFLICT')
                cached = json.loads(old['outcome_json'])
                return Outcome(cached['state'], cached['code'], cached.get('receipt_id'), cached.get('payload', {}))
            result = action(tx)
            if result.state == 'ACCEPTED':
                tx.execute('INSERT INTO artifacts_requests(idempotency_key,operation,input_sha256,outcome_json) VALUES(?,?,?,?)', (
                    cmd.idempotency_key, cmd.operation, command_digest,
                    _canon({'state': result.state, 'code': result.code, 'receipt_id': result.receipt_id, 'payload': result.payload}),
                ))  # type: ignore[attr-defined]
            return result
        return self.store.transact(run)

    def _fetch_observed(self, manifest: ArtifactManifest) -> tuple[bytes, Mapping[str, object]]:
        if self.readback is None:
            raise SchemaError('NOT_CONFIGURED', 'independent provider readback port absent')
        if manifest.size > self.max_bytes:
            raise SchemaError('BACKPRESSURE', 'artifact exceeds configured byte-validation limit')
        observed = self.readback.describe(manifest.dict())
        if not isinstance(observed, Mapping) or not {'provider', 'object_id', 'revision', 'parent_id', 'size'} <= observed.keys() or observed.keys() - {'provider', 'object_id', 'revision', 'parent_id', 'size', 'sha256'}:
            raise SchemaError('PROVIDER_UNKNOWN', 'provider readback metadata incomplete')
        if any(observed[k] != getattr(manifest, k) for k in ('provider', 'object_id', 'revision', 'parent_id', 'size')):
            raise SchemaError('DOCUMENT_CONFLICT', 'provider revision, parent, locator or size drift')
        if 'sha256' in observed and observed['sha256'] != manifest.sha256:
            raise SchemaError('SOURCE_SHA_MISMATCH', 'provider announced digest mismatch')
        data = self.readback.read_exact(manifest.dict())
        if isinstance(data, bytes):
            payload = data
        else:
            payload_parts: list[bytes] = []
            amount = 0
            for part in data:
                if not isinstance(part, bytes):
                    raise SchemaError('PROVIDER_UNKNOWN', 'readback yielded non-bytes')
                amount += len(part)
                if amount > self.max_bytes:
                    raise SchemaError('BACKPRESSURE', 'readback exceeds configured byte limit')
                payload_parts.append(part)
            payload = b''.join(payload_parts)
        if len(payload) != manifest.size or len(payload) == 0 or hashlib.sha256(payload).hexdigest() != manifest.sha256:
            raise SchemaError('SOURCE_SHA_MISMATCH', 'actual provider bytes do not match manifest')
        # Detect revision drift between metadata read and byte fetch.
        observed2 = self.readback.describe(manifest.dict())
        if not isinstance(observed2, Mapping) or set(observed2) != set(observed) or any(observed2.get(k) != observed.get(k) for k in ('provider', 'object_id', 'revision', 'parent_id', 'size', 'sha256')):
            raise SchemaError('DOCUMENT_CONFLICT', 'readback revision changed mid-verification')
        return payload, observed

    def execute(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        if not isinstance(command, CommandEnvelope) or command.version != 'NOMAD/CMD/1' or command.domain != 'artifacts' or command.operation not in {'artifacts.' + op for op in OPERATIONS} or not command.request_id or not command.idempotency_key or not isinstance(command.payload, dict):
            return _outcome('DENIED', 'MALFORMED_REQUEST')
        guarded = self._guard(identity, command.operation, str(command.payload.get('artifact_id', '')))
        if guarded:
            return guarded
        try:
            if command.operation == 'artifacts.register': return self._register(command, identity)
            if command.operation == 'artifacts.verify_bytes': return self._verify_bytes(command, identity)
            if command.operation == 'artifacts.validate_format': return self._validate_format(command, identity)
            if command.operation == 'artifacts.attach': return self._attach(command, identity)
            return self._publish_intent(command, identity)
        except SchemaError as ex:
            if ex.code in ('DOCUMENT_CONFLICT', 'SOURCE_SHA_MISMATCH', 'IDEMPOTENCY_CONFLICT'):
                return _outcome('CONFLICT', ex.code, reason=str(ex))
            if ex.code == 'PROVIDER_UNKNOWN':
                return _outcome('UNKNOWN', ex.code, reason=str(ex))
            if ex.code == 'BACKPRESSURE':
                return _outcome('BLOCKED', ex.code, reason=str(ex))
            return _error(ex)
        except Exception:
            # Provider, state or tool exceptions must never be reported as verified.
            return _outcome('UNKNOWN', 'PROVIDER_UNKNOWN', reason='operation failed; readback/reconciliation required')

    def _register(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        p = _strict_fields(cmd.payload, frozenset({'manifest'}), frozenset({'manifest'}))
        manifest = ArtifactManifest.parse(p['manifest'], self.folders)
        digest = manifest.digest()
        def action(tx: object) -> Outcome:
            old = tx.fetch_one('SELECT manifest_sha256 FROM artifacts_manifests WHERE artifact_id=?', (manifest.artifact_id,))  # type: ignore[attr-defined]
            if old:
                return _outcome('CONFLICT', 'DOCUMENT_CONFLICT', reason='artifact_id already registered; revisions require new ID')
            tx.execute('INSERT INTO artifacts_manifests(artifact_id,manifest_json,manifest_sha256,state,producer_principal,created_us) VALUES(?,?,?,?,?,?)', (manifest.artifact_id, _canon(manifest.dict()), digest, 'REGISTERED', identity.principal_id, self.now_us()))  # type: ignore[attr-defined]
            event_id = self._event(tx, identity, cmd, 'REGISTERED', {'artifact_id': manifest.artifact_id, 'subject_sha256': manifest.sha256})
            return Outcome('ACCEPTED', 'REGISTERED', event_id, {'artifact_id': manifest.artifact_id, 'manifest_sha256': digest, 'state': 'REGISTERED'})
        return self._mutation(cmd, identity, action)

    def _verify_bytes(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        p = _strict_fields(cmd.payload, frozenset({'artifact_id'}), frozenset({'artifact_id'}))
        art = _small_id(p['artifact_id'], 'artifact_id')
        row, manifest = self._get_manifest(art)
        _, meta = self._fetch_observed(manifest)
        if row['state'] in ('VERIFIED', 'VALIDATED') and row['byte_proof_sha256'] == manifest.sha256:
            return self._mutation(cmd, identity, lambda tx: Outcome('ACCEPTED', 'BYTES_ALREADY_VERIFIED', payload={'artifact_id': art, 'sha256': manifest.sha256}))
        proof = {'artifact_id': art, 'provider': manifest.provider, 'revision': manifest.revision, 'parent_id': manifest.parent_id, 'sha256': manifest.sha256, 'size': manifest.size, 'readback': dict(meta)}
        def action(tx: object) -> Outcome:
            old = tx.fetch_one('SELECT state,manifest_sha256 FROM artifacts_manifests WHERE artifact_id=?', (art,))  # type: ignore[attr-defined]
            if not old or old['manifest_sha256'] != manifest.digest():
                return _outcome('CONFLICT', 'DOCUMENT_CONFLICT')
            tx.execute("UPDATE artifacts_manifests SET state='VERIFIED',byte_proof_sha256=?,evidence_json=?,verified_us=? WHERE artifact_id=? AND state='REGISTERED'", (manifest.sha256, _canon(proof), self.now_us(), art))  # type: ignore[attr-defined]
            event_id = self._event(tx, identity, cmd, 'BYTES_VERIFIED', {'artifact_id': art, 'subject_sha256': manifest.sha256})
            return Outcome('ACCEPTED', 'BYTES_VERIFIED', event_id, {'artifact_id': art, 'sha256': manifest.sha256, 'revision': manifest.revision})
        return self._mutation(cmd, identity, action)

    def _validate_format(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        p = _strict_fields(cmd.payload, frozenset({'artifact_id'}), frozenset({'artifact_id'}))
        art = _small_id(p['artifact_id'], 'artifact_id')
        row, manifest = self._get_manifest(art)
        if row['state'] not in ('VERIFIED', 'VALIDATED') or row['byte_proof_sha256'] != manifest.sha256:
            return _outcome('BLOCKED', 'QA_PENDING', reason='first independently verify bytes')
        data, _ = self._fetch_observed(manifest)
        if row['state'] == 'VALIDATED':
            return self._mutation(cmd, identity, lambda tx: Outcome('ACCEPTED', 'FORMAT_ALREADY_VALIDATED', payload={'artifact_id': art, 'validator': row['validator']}))
        validator = validate(manifest, data, self.tools)
        def action(tx: object) -> Outcome:
            old = tx.fetch_one('SELECT state,byte_proof_sha256 FROM artifacts_manifests WHERE artifact_id=?', (art,))  # type: ignore[attr-defined]
            if not old or old['byte_proof_sha256'] != manifest.sha256:
                return _outcome('CONFLICT', 'DOCUMENT_CONFLICT')
            tx.execute("UPDATE artifacts_manifests SET state='VALIDATED',validator=?,validated_us=? WHERE artifact_id=? AND state='VERIFIED'", (validator, self.now_us(), art))  # type: ignore[attr-defined]
            event_id = self._event(tx, identity, cmd, 'FORMAT_VALIDATED', {'artifact_id': art, 'subject_sha256': manifest.sha256})
            return Outcome('ACCEPTED', 'FORMAT_VALIDATED', event_id, {'artifact_id': art, 'validator': validator})
        return self._mutation(cmd, identity, action)

    def _attach(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        p = _strict_fields(cmd.payload, frozenset({'artifact_id', 'job_id'}), frozenset({'artifact_id', 'job_id'}))
        art = _small_id(p['artifact_id'], 'artifact_id')
        job_id = _small_id(p['job_id'], 'job_id')
        if cmd.job_id != job_id:
            return _outcome('DENIED', 'INVALID_SCOPE', reason='job_id envelope mismatch')
        row, m = self._get_manifest(art)
        if row['state'] != 'VALIDATED':
            return _outcome('BLOCKED', 'QA_PENDING', reason='validated binary required')
        self._fetch_observed(m)
        attachment_id = hashlib.sha256(f'{art}\x00{job_id}'.encode()).hexdigest()[:32]
        subject = m.sha256
        evidence = {'artifact_id': art, 'job_id': job_id, 'subject_sha256': subject, 'manifest_sha256': m.digest(), 'producer_principal': row['producer_principal'], 'provider': m.provider, 'object_id': m.object_id, 'revision': m.revision, 'parent_id': m.parent_id, 'kind': m.kind, 'audio_review_required': m.kind in ('audio', 'fmod_bank', 'fmod_project'), 'fmod_contract': m.fmod_contract}
        publication_intent = {'artifact_id': art, 'job_id': job_id, 'source_sha256': subject, 'manifest_sha256': m.digest(), 'provider': m.provider, 'object_id': m.object_id, 'expected_revision': m.revision, 'parent_id': m.parent_id, 'size': m.size, 'mime': m.mime, 'qa_required': True, 'effect_key': 'artifact:' + attachment_id}
        def action(tx: object) -> Outcome:
            old = tx.fetch_one('SELECT * FROM artifacts_attachments WHERE artifact_id=? AND job_id=?', (art, job_id))  # type: ignore[attr-defined]
            if old:
                if old['subject_sha256'] != subject:
                    return _outcome('CONFLICT', 'DOCUMENT_CONFLICT')
                return Outcome('ACCEPTED', 'ALREADY_ATTACHED', payload={'attachment_id': old['attachment_id'], 'qa_receipt_id': old['qa_receipt_id']})
            tx.execute('INSERT INTO artifacts_attachments(attachment_id,artifact_id,job_id,actor_principal,subject_sha256,qa_intent_json,publication_intent_json,created_us) VALUES(?,?,?,?,?,?,?,?)', (attachment_id, art, job_id, identity.principal_id, subject, _canon(evidence), _canon(publication_intent), self.now_us()))  # type: ignore[attr-defined]
            event_id = self._event(tx, identity, cmd, 'ATTACHED_QA_PENDING', {'artifact_id': art, 'subject_sha256': subject})
            return Outcome('ACCEPTED', 'ATTACHED_QA_PENDING', event_id, {'attachment_id': attachment_id})
        local = self._mutation(cmd, identity, action)
        if local.state != 'ACCEPTED':
            return local
        if self.review is None:
            return _outcome('NOT_RUN', 'NOT_CONFIGURED', attachment_id=attachment_id, local_state='ATTACHED_QA_PENDING', missing='review.request_qa')
        qa_command = CommandEnvelope('NOMAD/CMD/1', 'artifacts-qa-'+attachment_id, 'review', 'review.request_qa', 'artifacts-qa-'+attachment_id, actor_claim=identity.principal_id, job_id=job_id, source_sha=subject, payload=evidence)
        try:
            res = self.review.execute(qa_command, identity)
        except Exception:
            return _outcome('UNKNOWN', 'PROVIDER_UNKNOWN', attachment_id=attachment_id, local_state='ATTACHED_QA_PENDING')
        if res.state != 'ACCEPTED' or not res.receipt_id:
            return _outcome(res.state if res.state in STATES else 'BLOCKED', res.code, attachment_id=attachment_id, local_state='ATTACHED_QA_PENDING')
        assert self.store is not None
        def record_qa(tx: object) -> None:
            changed = tx.execute('UPDATE artifacts_attachments SET qa_receipt_id=? WHERE attachment_id=? AND qa_receipt_id IS NULL', (res.receipt_id, attachment_id))
            if changed:
                self._event(tx, identity, cmd, 'QA_REQUESTED', {'artifact_id': art, 'subject_sha256': subject})
        self.store.transact(record_qa)
        return Outcome('ACCEPTED', 'QA_REQUESTED', res.receipt_id, {'attachment_id': attachment_id, 'artifact_id': art, 'subject_sha256': subject, 'publication_state': 'NOT_PREPARED'})

    def _publish_intent(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        p = _strict_fields(cmd.payload, frozenset({'artifact_id', 'job_id'}), frozenset({'artifact_id', 'job_id'}))
        art = _small_id(p['artifact_id'], 'artifact_id')
        job_id = _small_id(p['job_id'], 'job_id')
        if cmd.job_id != job_id:
            return _outcome('DENIED', 'INVALID_SCOPE')
        prior = self._prior_request(cmd, identity)
        if prior is not None: return prior
        row, m = self._get_manifest(art)
        if row['state'] != 'VALIDATED': return _outcome('BLOCKED', 'QA_PENDING')
        self._fetch_observed(m)
        assert self.store is not None
        attach = self.store.read(lambda r: r.fetch_one('SELECT * FROM artifacts_attachments WHERE artifact_id=? AND job_id=?', (art, job_id)))
        if attach is None or not attach['qa_receipt_id']:
            return _outcome('BLOCKED', 'QA_PENDING')
        if attach['publication_receipt_id']:
            return Outcome('ACCEPTED', 'PUBLICATION_ALREADY_PREPARED', attach['publication_receipt_id'], {'artifact_id': art})
        if self.review is None or self.publication is None:
            return _outcome('NOT_RUN', 'NOT_CONFIGURED', missing='review.verdict or publication.prepare')
        q = QueryEnvelope('NOMAD/QUERY/1', 'artifacts-verdict-'+attach['attachment_id'], 'review', 'review.verdict', job_id=job_id, payload={'artifact_id': art, 'subject_sha256': m.sha256, 'qa_receipt_id': attach['qa_receipt_id']})
        try:
            verdict = self.review.query(q, identity)
        except Exception:
            return _outcome('UNKNOWN', 'PROVIDER_UNKNOWN', reason='review readback unknown')
        v = verdict.payload
        if verdict.state != 'ACCEPTED' or not verdict.receipt_id or v.get('verdict') != 'PASS':
            return _outcome('BLOCKED', 'QA_PENDING')
        if any(v.get(k) != expected for k, expected in (('artifact_id', art), ('job_id', job_id), ('subject_sha256', m.sha256), ('qa_receipt_id', attach['qa_receipt_id']))):
            return _outcome('CONFLICT', 'DOCUMENT_CONFLICT', reason='QA verdict not bound to immutable subject and receipt')
        if not v.get('reviewer_principal') or v['reviewer_principal'] == row['producer_principal']:
            return _outcome('DENIED', 'SELF_REVIEW')
        intent = json.loads(attach['publication_intent_json'])
        intent['qa_verdict_receipt_id'] = verdict.receipt_id
        intent['qa_reviewer_principal'] = v['reviewer_principal']
        effect = 'artifacts-publication-'+attach['attachment_id']
        prepare = CommandEnvelope('NOMAD/CMD/1', effect, 'publication', 'publication.prepare', effect, actor_claim=identity.principal_id, job_id=job_id, source_sha=m.sha256, payload=intent)
        try:
            response = self.publication.execute(prepare, identity)
        except Exception:
            return _outcome('UNKNOWN', 'PROVIDER_UNKNOWN', reason='publication.prepare result unknown')
        if response.state != 'ACCEPTED' or not response.receipt_id:
            return _outcome(response.state if response.state in STATES else 'BLOCKED', response.code, publication_state='NOT_PREPARED')
        def update(tx: object) -> Outcome:
            tx.execute('UPDATE artifacts_attachments SET publication_receipt_id=? WHERE attachment_id=? AND publication_receipt_id IS NULL', (response.receipt_id, attach['attachment_id']))
            self._event(tx, identity, cmd, 'PUBLICATION_INTENT_PREPARED', {'artifact_id': art, 'subject_sha256': m.sha256})
            return Outcome('ACCEPTED', 'PUBLICATION_INTENT_PREPARED', response.receipt_id, {'artifact_id': art, 'subject_sha256': m.sha256, 'publication_state': 'PREPARED_NOT_PUBLISHED'})
        return self._mutation(cmd, identity, update)

    def query(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        if not isinstance(query, QueryEnvelope) or query.version != 'NOMAD/QUERY/1' or query.domain != 'artifacts' or query.operation != 'artifacts.proof' or not query.request_id:
            return _outcome('DENIED', 'MALFORMED_REQUEST')
        guarded = self._guard(identity, 'read', str(query.payload.get('artifact_id', '')) if isinstance(query.payload, dict) else '')
        if guarded:
            return guarded
        try:
            p = _strict_fields(query.payload, frozenset({'artifact_id'}), frozenset({'artifact_id'}))
            art = _small_id(p['artifact_id'], 'artifact_id')
            row, m = self._get_manifest(art)
            assert self.store is not None
            attachments = self.store.read(lambda r: r.fetch_all('SELECT attachment_id,job_id,qa_receipt_id,publication_receipt_id FROM artifacts_attachments WHERE artifact_id=? ORDER BY job_id LIMIT ?', (art, max(1, min(query.limit, 100)))))
            return Outcome('ACCEPTED', 'PROOF', payload={'manifest': m.dict(), 'manifest_sha256': m.digest(), 'state': row['state'], 'producer_principal': row['producer_principal'], 'byte_proof_sha256': row['byte_proof_sha256'], 'validator': row['validator'], 'readback_evidence': json.loads(row['evidence_json']) if row['evidence_json'] else None, 'attachments': list(attachments), 'qa_verdict': 'NOT_ASSERTED'})
        except SchemaError as ex:
            return _error(ex)
        except Exception:
            return _outcome('UNKNOWN', 'PROVIDER_UNKNOWN')


def build_component(deps: Mapping[str, object]) -> Component:
    """Create a WG-K facade; composition root supplies single-writer store and ports."""
    return ArtifactsComponent(deps)
