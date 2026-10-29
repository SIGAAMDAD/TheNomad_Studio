"""WG-S knowledge/canon authority. No provider calls inside StateStore transactions.

The sole SQL writer is supplied by the integration composition root. Provider readbacks
use the frozen Component.query port and never accept caller-provided content as proof.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

from nomad_studio.contracts.v1 import (
    CommandEnvelope, Component, DomainEvent, Outcome, QueryEnvelope, StateStore,
    VerifiedIdentity,
)

VERSION_CMD = 'NOMAD/CMD/1'
VERSION_QUERY = 'NOMAD/QUERY/1'
DOMAINS = frozenset({'design', 'narrative', 'gameplay', 'environment', 'fmod', 'art', 'qa'})
PUBLIC_CLASSES = frozenset({'PUBLIC', 'INTERNAL'})
SHA40 = re.compile(r'^[0-9a-f]{40}$')
SHA64 = re.compile(r'^[0-9a-f]{64}$')
SHA256 = SHA64
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,127}$')
MAX_DOCUMENT_BYTES = 262144
MAX_QUERY_BYTES = 65536
MAX_QUERY_TOKENS = 16384
MAX_ITEMS = 24
MAX_SCAN_ROWS = 512


class Reject(Exception):
    def __init__(self, state: str, code: str):
        super().__init__(code)
        self.state = state
        self.code = code


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode('ascii')).hexdigest()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def closed(value: Any, allowed: set[str], required: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(k, str) for k in value):
        raise Reject('DENIED', 'INVALID_SCHEMA')
    if set(value) - allowed or required - set(value):
        raise Reject('DENIED', 'INVALID_SCHEMA')
    return dict(value)


def string(value: Any, *, limit: int = 256, pattern: Any = None) -> str:
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > limit:
        raise Reject('DENIED', 'INVALID_SCHEMA')
    if pattern is not None and not pattern.fullmatch(value):
        raise Reject('DENIED', 'INVALID_SCHEMA')
    return value


def scoped_names(value: Any, *, max_count: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > max_count:
        raise Reject('DENIED', 'INVALID_SCHEMA')
    return tuple(sorted({string(s, limit=128, pattern=NAME) for s in value}))


def current_us() -> int:
    return time.time_ns() // 1000


def allowed(identity: VerifiedIdentity, scope: str) -> bool:
    return scope in identity.scopes


def principal(identity: Any, scope: str) -> None:
    if (not isinstance(identity, VerifiedIdentity) or
            not identity.principal_id or not identity.credential_id or
            not identity.issuer_id or not identity.authenticated_via or
            type(identity.current_authority_epoch) is not int or
            identity.current_authority_epoch < 0 or
            type(identity.expires_at_us) is not int or
            identity.expires_at_us <= current_us() or
            not isinstance(identity.scopes, frozenset)):
        raise Reject('DENIED', 'IDENTITY_UNVERIFIED')
    if not allowed(identity, scope):
        raise Reject('DENIED', 'INVALID_SCOPE')


def check_envelope(env: Any, *, command: bool) -> str:
    if command:
        if not isinstance(env, CommandEnvelope) or env.version != VERSION_CMD or not env.idempotency_key:
            raise Reject('DENIED', 'MALFORMED_REQUEST')
    else:
        if (not isinstance(env, QueryEnvelope) or env.version != VERSION_QUERY or
                type(env.limit) is not int or not 1 <= env.limit <= 1000):
            raise Reject('DENIED', 'MALFORMED_REQUEST')
    if env.domain != 'knowledge' or not isinstance(env.request_id, str) or not env.request_id:
        raise Reject('DENIED', 'MALFORMED_REQUEST')
    if not isinstance(env.operation, str) or not env.operation.startswith('knowledge.'):
        raise Reject('DENIED', 'MALFORMED_REQUEST')
    if not isinstance(env.payload, Mapping):
        raise Reject('DENIED', 'INVALID_SCHEMA')
    try:
        if len(canonical(env.payload).encode('ascii')) > MAX_DOCUMENT_BYTES * 2:
            raise Reject('DENIED', 'BACKPRESSURE')
    except (ValueError, TypeError, RecursionError):
        raise Reject('DENIED', 'INVALID_SCHEMA') from None
    return env.operation.removeprefix('knowledge.')


def source_ref(value: Any) -> tuple[str, str, str, dict[str, str]]:
    src = closed(value, {'type', 'repo', 'path', 'ref', 'commit_sha', 'blob_sha', 'file_id', 'revision_id'}, {'type'})
    kind = src['type']
    if kind == 'git':
        if set(src) != {'type', 'repo', 'path', 'ref', 'commit_sha', 'blob_sha'}:
            raise Reject('DENIED', 'INVALID_SCHEMA')
        repo = string(src['repo'], limit=200)
        if repo.count('/') != 1 or not all(NAME.fullmatch(s) for s in repo.split('/')):
            raise Reject('DENIED', 'INVALID_SCHEMA')
        path = string(src['path'], limit=512)
        if path.startswith('/') or '\\' in path or any(x in {'', '.', '..'} for x in path.split('/')):
            raise Reject('DENIED', 'INVALID_PATH')
        ref = string(src['ref'], limit=128)
        if ref.startswith('-') or '..' in ref or any(c in ref for c in ['\\', '^', '~', ':', '?', '*', '[', ' ', '@{']):
            raise Reject('DENIED', 'INVALID_SCHEMA')
        commit = string(src['commit_sha'], limit=64)
        blob = string(src['blob_sha'], limit=64)
        if not ((SHA40.fullmatch(commit) and SHA40.fullmatch(blob)) or
                (SHA64.fullmatch(commit) and SHA64.fullmatch(blob))):
            raise Reject('DENIED', 'INVALID_SCHEMA')
        identity = {'type': 'git', 'repo': repo, 'path': path, 'ref': ref}
        revision = commit + ':' + blob
    elif kind == 'drive':
        if set(src) != {'type', 'file_id', 'revision_id'}:
            raise Reject('DENIED', 'INVALID_SCHEMA')
        file_id = string(src['file_id'], limit=256)
        revision = string(src['revision_id'], limit=256)
        identity = {'type': 'drive', 'file_id': file_id}
    else:
        raise Reject('DENIED', 'INVALID_SCHEMA')
    return kind, digest(identity), revision, {k: str(v) for k, v in src.items()}


def location_identity(source: dict[str, str]) -> dict[str, str]:
    if source['type'] == 'git':
        return {k: source[k] for k in ('type', 'repo', 'path', 'ref')}
    return {k: source[k] for k in ('type', 'file_id')}


def outcome_error(error: Reject) -> Outcome:
    return Outcome(error.state, error.code)


@dataclass(slots=True)
class KnowledgeComponent:
    store: StateStore | None
    providers: Mapping[str, Component]
    owner_principals: frozenset[str]
    _service_identity: VerifiedIdentity | None = None
    name: str = 'knowledge'

    def _readback(self, source: dict[str, str]) -> dict[str, Any]:
        kind, _, expected_revision, _ = source_ref(source)
        provider = self.providers.get(kind)
        if provider is None:
            raise Reject('NOT_RUN', 'NOT_CONFIGURED')
        # These are frozen Component.query operations; the response envelope is
        # a documented integration adapter contract, not a new shared Python ABI.
        if kind == 'git':
            domain, operation = 'source', 'source.resolve'
        else:
            domain, operation = 'artifacts', 'artifacts.proof'
        q = QueryEnvelope(version=VERSION_QUERY, request_id='knowledge-readback-'+digest(source),
                          domain=domain, operation=operation, limit=1,
                          payload={'source': source, 'verify_current': True})
        # No worker identity may be passed as source-provider authorization.
        # A provider adapter has a service-scoped principal, provisioned only by
        # the integration root; this module cannot mint one itself.
        try:
            response = provider.query(q, self._provider_identity)
        except (OSError, TimeoutError, ConnectionError):
            raise Reject('UNKNOWN', 'PROVIDER_UNKNOWN') from None
        if isinstance(response, Outcome) and response.state == 'NOT_RUN':
            raise Reject('NOT_RUN', 'NOT_CONFIGURED')
        if not isinstance(response, Outcome) or response.state != 'ACCEPTED':
            raise Reject('UNKNOWN', 'PROVIDER_UNKNOWN')
        try:
            proof = closed(response.payload,
                           {'source', 'current_revision', 'content', 'content_sha256',
                            'classification', 'readback_id', 'verified', 'folder_ref'},
                           {'source', 'current_revision', 'content', 'content_sha256',
                            'classification', 'readback_id', 'verified'})
            if proof['verified'] is not True or proof['source'] != source:
                raise Reject('UNKNOWN', 'PROVIDER_UNKNOWN')
            revision = string(proof['current_revision'], limit=256)
            if not isinstance(proof['content'], str):
                raise Reject('UNKNOWN', 'PROVIDER_UNKNOWN')
            raw = proof['content'].encode('utf-8')
            if len(raw) > MAX_DOCUMENT_BYTES:
                raise Reject('DENIED', 'BACKPRESSURE')
            if not SHA256.fullmatch(string(proof['content_sha256'], limit=64)) or sha(raw) != proof['content_sha256']:
                raise Reject('UNKNOWN', 'SOURCE_SHA_MISMATCH')
            if string(proof['classification'], limit=32) not in PUBLIC_CLASSES:
                raise Reject('DENIED', 'INVALID_SCOPE')
            string(proof['readback_id'], limit=256)
            if kind == 'git' and len(source['blob_sha']) == 40:
                actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
                if actual != source['blob_sha']:
                    raise Reject('UNKNOWN', 'SOURCE_SHA_MISMATCH')
            if kind == 'git' and len(source['blob_sha']) == 64:
                actual = hashlib.sha256(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
                if actual != source['blob_sha']:
                    raise Reject('UNKNOWN', 'SOURCE_SHA_MISMATCH')
            if revision != expected_revision:
                return {'changed_revision': revision}
            return proof
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise Reject('UNKNOWN', 'PROVIDER_UNKNOWN') from None

    @property
    def _provider_identity(self) -> VerifiedIdentity:
        # Only an integration-root-injected service principal can call provider ports.
        # This property is overwritten at construction via service_identity.
        identity = getattr(self, '_service_identity', None)
        if identity is None:
            raise Reject('NOT_RUN', 'NOT_CONFIGURED')
        principal(identity, 'knowledge:provider:readback')
        return identity

    def _store(self) -> StateStore:
        if self.store is None:
            raise Reject('NOT_RUN', 'NOT_CONFIGURED')
        return self.store

    def _event(self, command: CommandEnvelope, identity: VerifiedIdentity,
               kind: str, obj: Mapping[str, Any], tx: Any) -> str:
        return tx.append_event(DomainEvent(
            event_id='knowledge-'+digest({'request': command.request_id,
                                         'idempotency': command.idempotency_key, 'kind': kind}),
            domain='knowledge', kind=kind, actor_principal_id=identity.principal_id,
            issued_at_us=current_us(), correlation_id=command.request_id,
            job_id=command.job_id, shift_id=command.shift_id,
            assignment_epoch=command.assignment_epoch,
            payload_sha256=digest(obj), payload={'receipt_digest': digest(obj)}))

    @staticmethod
    def _fingerprint(cmd: CommandEnvelope, identity: VerifiedIdentity) -> str:
        return digest({'principal': identity.principal_id, 'operation': cmd.operation,
                       'payload': cmd.payload, 'job_id': cmd.job_id})

    def _replay(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome | None:
        row = self._store().read(lambda r: r.fetch_one(
            'SELECT * FROM knowledge_idempotency WHERE key=?',
            ('knowledge:' + cmd.idempotency_key,)))
        if row is None:
            return None
        if row['fingerprint'] != self._fingerprint(cmd, identity):
            return Outcome('CONFLICT','IDEMPOTENCY_CONFLICT')
        return Outcome(row['state'],row['code'],row['receipt_id'],json.loads(row['payload_json']))

    def _mutate(self, cmd: CommandEnvelope, identity: VerifiedIdentity, fn: Any) -> Outcome:
        key = 'knowledge:' + cmd.idempotency_key
        fingerprint = self._fingerprint(cmd, identity)
        def transaction(tx: Any) -> Outcome:
            past = tx.fetch_one('SELECT * FROM knowledge_idempotency WHERE key = ?', (key,))
            if past:
                if past['fingerprint'] != fingerprint:
                    return Outcome('CONFLICT', 'IDEMPOTENCY_CONFLICT')
                return Outcome(past['state'], past['code'], past['receipt_id'], json.loads(past['payload_json']))
            result = fn(tx)
            if result.state == 'ACCEPTED':
                tx.execute('INSERT INTO knowledge_idempotency (key,fingerprint,state,code,receipt_id,payload_json) '
                           'VALUES (?,?,?,?,?,?)', (key, fingerprint, result.state,
                            result.code, result.receipt_id, canonical(result.payload)))
            return result
        return self._store().transact(transaction, idempotency_key=key)

    def execute(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        try:
            op = check_envelope(command, command=True)
            scope = {'index':'knowledge:index', 'invalidate':'knowledge:invalidate',
                     'accept_canon':'knowledge:accept_canon'}.get(op)
            if scope is None:
                raise Reject('DENIED', 'NOT_SUPPORTED')
            principal(identity, scope)
            existing = self._replay(command, identity)
            if existing is not None:
                return existing
            if op == 'index':
                return self._index(command, identity)
            if op == 'invalidate':
                return self._invalidate(command, identity)
            return self._accept_canon(command, identity)
        except Reject as exc:
            return outcome_error(exc)
        except (OSError, TimeoutError, ConnectionError):
            return Outcome('BLOCKED', 'STORE_FAILURE')

    def _index(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        p = closed(cmd.payload, {'source', 'domain', 'subject', 'folder_ref', 'roles',
                                 'departments', 'jobs', 'read_scopes'},
                   {'source','domain','subject','folder_ref','roles','departments','jobs','read_scopes'})
        kind, source_key, revision, src = source_ref(p['source'])
        domain = string(p['domain'], limit=32)
        if domain not in DOMAINS:
            raise Reject('DENIED', 'INVALID_SCHEMA')
        subject = string(p['subject'], limit=128, pattern=NAME)
        folder = string(p['folder_ref'], limit=512)
        if '/oversight/' in ('/'+folder.strip('/')+'/').lower() or folder.lower().startswith('oversight:'):
            raise Reject('DENIED', 'INVALID_SCOPE')
        roles, departments, jobs, reads = [scoped_names(p[k]) for k in ('roles','departments','jobs','read_scopes')]
        if any(v.lower().startswith('oversight') for v in roles + departments + reads):
            raise Reject('DENIED', 'INVALID_SCOPE')
        if any(not s.startswith('knowledge:') for s in reads):
            raise Reject('DENIED', 'INVALID_SCHEMA')
        proof = self._readback(src)
        if 'changed_revision' in proof:
            self._mark_changed(source_key,proof['changed_revision'])
            raise Reject('CONFLICT', 'REBASE_REQUIRED')
        if 'folder_ref' in proof and proof['folder_ref'] != folder:
            raise Reject('CONFLICT', 'DOCUMENT_CONFLICT')
        content = proof['content']
        if not content.strip():
            raise Reject('DENIED', 'INVALID_SCHEMA')
        meta = {'source': src, 'subject': subject, 'domain': domain, 'folder_ref': folder,
                'roles':roles, 'departments': departments, 'jobs': jobs, 'read_scopes':reads,
                'classification':proof['classification'], 'content_sha256':proof['content_sha256']}
        metadata_sha = digest(meta)
        record_id = digest({'source_key':source_key, 'revision':revision})
        previous=self._store().read(lambda r:r.fetch_one(
            'SELECT content_sha256 FROM knowledge_sources WHERE source_key=? AND revision=?',
            (source_key,revision)))
        if previous and previous['content_sha256'] != proof['content_sha256']:
            self._mark_unverified(source_key)
            raise Reject('CONFLICT','SOURCE_SHA_MISMATCH')
        def action(tx: Any) -> Outcome:
            prev = tx.fetch_one('SELECT * FROM knowledge_sources WHERE source_key=? AND revision=?',
                                (source_key, revision))
            if prev and prev['metadata_sha256'] != metadata_sha:
                return Outcome('CONFLICT', 'DOCUMENT_CONFLICT')
            if not prev:
                tx.execute('INSERT INTO knowledge_sources '
                    '(record_id,source_key,revision,source_type,source_json,subject,domain,body,'
                    'content_sha256,provenance_id,classification,folder_ref,roles_json,'
                    'departments_json,jobs_json,read_scopes_json,metadata_sha256) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (record_id,source_key,revision,kind,canonical(src),subject,domain,content,
                     proof['content_sha256'],proof['readback_id'],proof['classification'],folder,
                     canonical(roles),canonical(departments),canonical(jobs),canonical(reads), metadata_sha))
            head = tx.fetch_one('SELECT * FROM knowledge_heads WHERE source_key=?', (source_key,))
            if not head:
                tx.execute('INSERT INTO knowledge_heads (source_key,current_revision,latest_record_id) '
                           'VALUES (?,?,?)', (source_key,revision,record_id))
            elif head['current_revision'] != revision or head['latest_record_id'] != record_id:
                tx.execute('UPDATE knowledge_heads SET current_revision=?,latest_record_id=? '
                           'WHERE source_key=?', (revision,record_id,source_key))
                self._stale_exports(tx, source_key)
            if not head or (not prev and head['current_revision'] == revision):
                self._bump_epoch(tx)
            audit = self._event(cmd,identity,'indexed',{'record_id':record_id,'source':src},tx)
            return Outcome('ACCEPTED', 'INDEXED',audit,{'record_id':record_id,
                 'source_key':source_key,'revision':revision,'content_sha256':proof['content_sha256']})
        return self._mutate(cmd,identity,action)

    @staticmethod
    def _bump_epoch(tx: Any) -> None:
        # Conservative invalidation also catches previously empty exports.
        tx.execute('UPDATE knowledge_epochs SET generation=generation+1 WHERE id=1')
        tx.execute("UPDATE knowledge_exports SET state='STALE' WHERE state='CURRENT'")

    @staticmethod
    def _stale_exports(tx: Any, source_key: str) -> None:
        # Source-specific callers still use global invalidation because a source
        # becoming available can change previously empty, filtered projections.
        KnowledgeComponent._bump_epoch(tx)

    def _mark_changed(self, source_key: str, latest: str) -> None:
        def txfn(tx: Any) -> None:
            head = tx.fetch_one('SELECT * FROM knowledge_heads WHERE source_key=?', (source_key,))
            if head and head['current_revision'] != latest:
                tx.execute('UPDATE knowledge_heads SET current_revision=?,latest_record_id=NULL '
                           'WHERE source_key=?', (latest,source_key))
                self._stale_exports(tx,source_key)
        self._store().transact(txfn)

    def _mark_unverified(self, source_key: str) -> None:
        def txfn(tx: Any) -> None:
            head=tx.fetch_one('SELECT * FROM knowledge_heads WHERE source_key=?',(source_key,))
            if head and head['latest_record_id'] is not None:
                tx.execute('UPDATE knowledge_heads SET latest_record_id=NULL WHERE source_key=?',
                           (source_key,))
                self._bump_epoch(tx)
        self._store().transact(txfn)

    def _check_record(self, row: Mapping[str, Any]) -> None:
        try:
            proof=self._readback(json.loads(row['source_json']))
        except Reject as exc:
            if exc.code in {'SOURCE_SHA_MISMATCH','INVALID_SCOPE'}:
                self._mark_unverified(row['source_key'])
            raise
        if 'changed_revision' in proof:
            self._mark_changed(row['source_key'],proof['changed_revision'])
            raise Reject('CONFLICT','REBASE_REQUIRED')
        if proof['content_sha256'] != row['content_sha256']:
            self._mark_unverified(row['source_key'])
            raise Reject('CONFLICT','SOURCE_SHA_MISMATCH')
        return proof

    def _invalidate(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        p = closed(cmd.payload, {'source'}, {'source'})
        _, key, rev, src = source_ref(p['source'])
        proof = self._readback(src)
        changed = proof.get('changed_revision')
        def action(tx: Any) -> Outcome:
            head = tx.fetch_one('SELECT * FROM knowledge_heads WHERE source_key=?', (key,))
            if not head:
                return Outcome('UNKNOWN', 'NOT_CONFIGURED')
            observed = changed or rev
            if head['current_revision'] != observed:
                tx.execute('UPDATE knowledge_heads SET current_revision=?,latest_record_id=NULL '
                           'WHERE source_key=?', (observed,key))
                self._stale_exports(tx,key)
            receipt = self._event(cmd,identity,'revision_observed',
                                  {'source_key':key,'current_revision':observed},tx)
            return Outcome('ACCEPTED','INVALIDATED' if head['current_revision'] != observed else 'UNCHANGED',
                           receipt, {'source_key':key,'revision':observed})
        return self._mutate(cmd,identity,action)

    def _accept_canon(self, cmd: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        if (identity.principal_id not in self.owner_principals or
                'owner:canon' not in identity.scopes):
            raise Reject('DENIED', 'OWNER_APPROVAL_REQUIRED')
        p = closed(cmd.payload, {'subject','record_id','approval_reference'},
                   {'subject','record_id','approval_reference'})
        subject = string(p['subject'],limit=128,pattern=NAME)
        record_id = string(p['record_id'],limit=64,pattern=SHA256)
        approval_ref = string(p['approval_reference'],limit=256)
        row = self._store().read(lambda r: r.fetch_one(
            'SELECT s.*,h.current_revision,h.latest_record_id FROM knowledge_sources s '
            'LEFT JOIN knowledge_heads h ON h.source_key=s.source_key WHERE s.record_id=?',
            (record_id,)))
        if not row or row['subject'] != subject:
            raise Reject('DENIED','INVALID_SCHEMA')
        if row['current_revision'] != row['revision'] or row['latest_record_id'] != record_id:
            raise Reject('CONFLICT','REBASE_REQUIRED')
        self._check_record(row)
        def action(tx: Any) -> Outcome:
            h = tx.fetch_one('SELECT * FROM knowledge_heads WHERE source_key=?', (row['source_key'],))
            if not h or h['current_revision'] != row['revision'] or h['latest_record_id'] != record_id:
                return Outcome('CONFLICT','REBASE_REQUIRED')
            current = tx.fetch_one('SELECT * FROM knowledge_canon WHERE subject=?', (subject,))
            if current and current['record_id'] == record_id:
                return Outcome('ACCEPTED','CANON_UNCHANGED',
                               payload={'subject':subject,'record_id':record_id,'generation':current['generation']})
            generation = current['generation'] + 1 if current else 1
            if current:
                tx.execute('UPDATE knowledge_canon SET record_id=?,generation=? WHERE subject=?',
                           (record_id,generation,subject))
            else:
                tx.execute('INSERT INTO knowledge_canon (subject,record_id,generation) VALUES (?,?,?)',
                           (subject,record_id,generation))
            tx.execute('INSERT INTO knowledge_canon_history '
                       '(subject,generation,record_id,owner_principal,accepted_at_us,approval_reference) '
                       'VALUES (?,?,?,?,?,?)',
                       (subject,generation,record_id,identity.principal_id,current_us(),approval_ref))
            self._bump_epoch(tx)
            receipt=self._event(cmd,identity,'canon_accepted',
                  {'record_id':record_id,'subject':subject,'generation':generation},tx)
            return Outcome('ACCEPTED','CANON_ACCEPTED',receipt,
                   {'subject':subject,'record_id':record_id,'generation':generation})
        return self._mutate(cmd,identity,action)

    def query(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        try:
            op = check_envelope(query,command=False)
            if op not in {'resolve','context'}:
                raise Reject('DENIED','NOT_SUPPORTED')
            principal(identity,'knowledge:read')
            if op == 'resolve':
                return self._resolve(query,identity)
            return self._context(query,identity)
        except Reject as exc:
            return outcome_error(exc)
        except (OSError, TimeoutError, ConnectionError):
            return Outcome('UNKNOWN','PROVIDER_UNKNOWN')

    @staticmethod
    def _authorize(row: Mapping[str,Any], identity: VerifiedIdentity,
                   role: str | None = None, department: str | None = None,
                   job: str | None = None) -> bool:
        required = json.loads(row['read_scopes_json'])
        if not all(s in identity.scopes for s in required):
            return False
        for name,val in (('roles_json',role),('departments_json',department),('jobs_json',job)):
            filters=json.loads(row[name])
            if filters and (val is None or val not in filters):
                return False
        return True

    def _resolve(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        p = closed(query.payload, {'subject','include_history','role','department','job_id'}, {'subject'})
        subject=string(p['subject'],limit=128,pattern=NAME)
        hist=p.get('include_history',False)
        if type(hist) is not bool:
            raise Reject('DENIED','INVALID_SCHEMA')
        if hist and 'knowledge:history' not in identity.scopes:
            raise Reject('DENIED','INVALID_SCOPE')
        role=p.get('role'); dept=p.get('department'); job=p.get('job_id')
        if role is not None:
            role=string(role,limit=128,pattern=NAME)
            if not ('knowledge:role:'+role in identity.scopes or 'knowledge:role:*' in identity.scopes):
                raise Reject('DENIED','INVALID_SCOPE')
        if dept is not None:
            dept=string(dept,limit=128,pattern=NAME)
            if not ('knowledge:department:'+dept in identity.scopes or 'knowledge:department:*' in identity.scopes):
                raise Reject('DENIED','INVALID_SCOPE')
        if job is not None:
            job=string(job,limit=128,pattern=NAME)
            if query.job_id != job or not ('knowledge:job:'+job in identity.scopes or 'knowledge:job:*' in identity.scopes):
                raise Reject('DENIED','INVALID_SCOPE')
        rows=self._store().read(lambda r:r.fetch_all(
            'SELECT s.*,h.current_revision,h.latest_record_id,c.generation, '
            'CASE WHEN c.record_id=s.record_id THEN 1 ELSE 0 END AS is_canon, '
            '(SELECT MAX(ch.generation) FROM knowledge_canon_history ch '
            ' WHERE ch.record_id=s.record_id AND ch.subject=s.subject) AS accepted_generation '
            'FROM knowledge_sources s LEFT JOIN knowledge_heads h ON h.source_key=s.source_key '
            'LEFT JOIN knowledge_canon c ON c.subject=s.subject WHERE s.subject=? ORDER BY s.record_id',
            (subject,)))
        result=[]
        for row in rows:
            if not self._authorize(row,identity,role,dept,job):
                continue
            if not hist and not row['is_canon']:
                continue
            status = 'STALE' if row['current_revision'] != row['revision'] else (
                     'CURRENT' if row['latest_record_id'] == row['record_id'] else 'HISTORICAL')
            result.append({'record_id':row['record_id'],'source':json.loads(row['source_json']),
                           'domain':row['domain'],'subject':row['subject'],
                           'content_sha256':row['content_sha256'],
                           'canon':bool(row['is_canon']),'accepted_generation':row['accepted_generation'],
                           'status':status})
            if len(result) >= min(MAX_ITEMS,max(1,query.limit)):
                break
        # A resolve result is a metadata view, not a live verification claim.
        return Outcome('ACCEPTED','RESOLVED',payload={'records':result})

    def _context(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        p=closed(query.payload, {'role','department','job_id','domains','topic','max_bytes',
                                 'max_tokens','max_items','include_references'}, {'role','department','domains','max_bytes','max_tokens'})
        role=string(p['role'],limit=128,pattern=NAME)
        dept=string(p['department'],limit=128,pattern=NAME)
        job=p.get('job_id')
        if job is not None:
            job=string(job,limit=128,pattern=NAME)
        if not ('knowledge:role:'+role in identity.scopes or 'knowledge:role:*' in identity.scopes):
            raise Reject('DENIED','INVALID_SCOPE')
        if not ('knowledge:department:'+dept in identity.scopes or 'knowledge:department:*' in identity.scopes):
            raise Reject('DENIED','INVALID_SCOPE')
        if job is not None and (query.job_id != job or
                ('knowledge:job:'+job not in identity.scopes and 'knowledge:job:*' not in identity.scopes)):
            raise Reject('DENIED','INVALID_SCOPE')
        include_references=p.get('include_references',False)
        if type(include_references) is not bool:
            raise Reject('DENIED','INVALID_SCHEMA')
        if include_references and 'knowledge:reference:read' not in identity.scopes:
            raise Reject('DENIED','INVALID_SCOPE')
        domains=scoped_names(p['domains'],max_count=len(DOMAINS))
        if not domains or any(d not in DOMAINS for d in domains):
            raise Reject('DENIED','INVALID_SCHEMA')
        topic=p.get('topic','')
        if not isinstance(topic,str) or len(topic.encode('utf-8'))>128:
            raise Reject('DENIED','INVALID_SCHEMA')
        byte_budget=p['max_bytes']; token_budget=p['max_tokens']; max_items=p.get('max_items',MAX_ITEMS)
        if (type(byte_budget) is not int or byte_budget<512 or byte_budget>MAX_QUERY_BYTES or
            type(token_budget) is not int or token_budget<512 or token_budget>MAX_QUERY_TOKENS or
            type(max_items) is not int or not 1<=max_items<=MAX_ITEMS):
            raise Reject('DENIED','INVALID_SCHEMA')
        sql=(
            'SELECT s.record_id,s.source_key,s.revision,s.source_json,s.subject,s.domain,'
            's.content_sha256,s.provenance_id,s.roles_json,s.departments_json,s.jobs_json,'
            's.read_scopes_json,h.current_revision,h.latest_record_id,c.generation,'
            'CASE WHEN c.record_id=s.record_id THEN 1 ELSE 0 END AS is_canon '
            'FROM knowledge_sources s '
            'JOIN knowledge_heads h ON h.source_key=s.source_key '
            'LEFT JOIN knowledge_canon c ON c.subject=s.subject '
            'WHERE s.domain IN ('+','.join('?' for _ in domains)+') '
            "AND (? = '' OR instr(lower(s.subject),lower(?))>0 OR instr(lower(s.body),lower(?))>0) "
            'ORDER BY s.domain,s.subject,is_canon DESC,s.record_id LIMIT ?')
        epoch,rows=self._store().read(lambda r:(
            r.fetch_one('SELECT generation FROM knowledge_epochs WHERE id=1')['generation'],
            r.fetch_all(sql,(*domains,topic,topic,topic,MAX_SCAN_ROWS+1))))
        if len(rows)>MAX_SCAN_ROWS:
            raise Reject('DENIED','BACKPRESSURE')
        selected=[]
        more_entries=False
        for row in rows:
            if not self._authorize(row,identity,role,dept,job):
                continue
            if not row['is_canon'] and not include_references:
                continue
            if not row['is_canon'] and row['latest_record_id'] != row['record_id']:
                continue
            if row['current_revision'] != row['revision'] or row['latest_record_id'] != row['record_id']:
                raise Reject('CONFLICT','REBASE_REQUIRED')
            if len(selected)>=max_items:
                more_entries=True
                break
            selected.append(row)
        # Verify only the documents that can fit in this explicitly truncated
        # export. Provider content supplies snippets, avoiding bulk DB body loads.
        live_body={}
        for row in selected:
            proof=self._check_record(row)
            live_body[row['record_id']]=proof['content']
        entries=[]
        meta={'version':'NOMAD/KNOWLEDGE-CONTEXT/1','role':role,'department':dept,
              'job_id':job,'domains':list(domains),'include_references':include_references,
              'epoch':epoch,'entries':entries,'truncated':False,
              'digest':'0'*64,'byte_count':0,'token_upper_bound':0}
        # 1 UTF-8 output byte per possible token is deliberately conservative;
        # no tokenizer dependency or fabricated exact model token count.
        limit=min(byte_budget,token_budget)
        for row in selected:
            if len(entries)>=max_items:
                meta['truncated']=True
                break
            ref={'record_id':row['record_id'],'source':json.loads(row['source_json']),
                 'content_sha256':row['content_sha256'],'provenance_id':row['provenance_id']}
            # snippets are verified provider text and always carry exact refs.
            text=live_body[row['record_id']].strip()
            entry={'subject':row['subject'],'domain':row['domain'],
                   'authority':'CANON' if row['is_canon'] else 'REFERENCE_UNAPPROVED',
                   'canon_generation':row['generation'] if row['is_canon'] else None,
                   'citation':ref,'snippet':''}
            base=canonical(meta)
            room=limit-len(base.encode('ascii'))-len(canonical(entry).encode('ascii'))-48
            if room<16:
                meta['truncated']=True
                break
            # For JSON escaping, binary search maximum prefix that fits the entire bundle.
            low,high=0,len(text)
            while low<high:
                mid=(low+high+1)//2
                entry['snippet']=text[:mid]
                test=entries+[entry]
                provisional={**meta,'entries':test,'truncated':True}
                if len(canonical(provisional).encode('ascii'))<=limit-128:
                    low=mid
                else:
                    high=mid-1
            if low==0:
                meta['truncated']=True
                break
            entry['snippet']=text[:low]
            entries.append(entry)
            if low<len(text):
                meta['truncated']=True
                break
        if len(entries)<len(selected) or more_entries:
            meta['truncated']=True
        # Hash excludes the self-referential hash and counters.
        stable={k:v for k,v in meta.items() if k not in {'digest','byte_count','token_upper_bound'}}
        meta['digest']=digest(stable)
        meta['byte_count']=len(canonical(meta).encode('ascii'))
        meta['token_upper_bound']=meta['byte_count']
        # Byte-count digit-length may change after being inserted.
        for _ in range(3):
            count=len(canonical(meta).encode('ascii'))
            meta['byte_count']=count
            meta['token_upper_bound']=count
        if meta['byte_count']>limit:
            raise Reject('DENIED','BACKPRESSURE')
        source_keys=sorted({r['source_key'] for r in selected[:len(entries)]})
        record_ids=sorted(e['citation']['record_id'] for e in entries)
        def save(tx: Any) -> None:
            gen=tx.fetch_one('SELECT generation FROM knowledge_epochs WHERE id=1')
            if gen is None or gen['generation'] != epoch:
                raise Reject('CONFLICT','REBASE_REQUIRED')
            old=tx.fetch_one('SELECT * FROM knowledge_exports WHERE digest=?',(meta['digest'],))
            if old and old['state']=='STALE':
                raise Reject('CONFLICT','REBASE_REQUIRED')
            if not old:
                tx.execute('INSERT INTO knowledge_exports (digest,source_keys_json,record_ids_json,state) '
                           "VALUES (?,?,?,'CURRENT')",
                           (meta['digest'],canonical(source_keys),canonical(record_ids)))
        self._store().transact(save)
        return Outcome('ACCEPTED','CONTEXT_READY',receipt_id=meta['digest'],payload=meta)


def build_component(deps: Mapping[str, object]) -> Component:
    """Return a WG-S Component with no internally created storage/provider credentials.

    Required for functioning mutating/reading operations:
      store: frozen StateStore; source: Git readback Component;
      artifacts: Drive readback Component; service_identity: trusted readback
      VerifiedIdentity; owner_principals: explicitly configured owner IDs.
    Missing optional provider -> NOT_RUN for that source; no simulated success.
    """
    if not isinstance(deps, Mapping):
        raise TypeError('deps must be a mapping')
    owners=deps.get('owner_principals',())
    if not isinstance(owners,(frozenset,set,tuple,list)) or not all(isinstance(o,str) for o in owners):
        raise TypeError('owner_principals must be a collection of principal identifiers')
    component=KnowledgeComponent(
        store=deps.get('store'),
        providers={'git':deps.get('source'), 'drive':deps.get('artifacts')},
        owner_principals=frozenset(owners))
    service_identity=deps.get('service_identity')
    if service_identity is not None and not isinstance(service_identity,VerifiedIdentity):
        raise TypeError('service_identity must be a VerifiedIdentity')
    component._service_identity = service_identity
    return component
