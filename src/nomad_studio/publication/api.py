"""WG-J transactional publication outbox. No provider IO runs in a SQL transaction."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from nomad_studio.contracts.v1 import (CommandEnvelope, QueryEnvelope,
    VerifiedIdentity, Outcome, DomainEvent)
from .model import (ValidationError, ProviderConflict, ProviderUnknown,
    ProviderForbidden, ProviderRateLimited, Observation, canonical,
    fields, fingerprint, ident, positive, validate_prepare, digest)
from .providers import GitHubAdapter, DriveAdapter

STATES = {'PREPARED','DISPATCHING','PENDING_UNKNOWN','EXTERNALLY_OBSERVED',
          'VERIFIED','CONFLICT','BLOCKED'}
OPERATIONS = {'publication.prepare','publication.dispatch','publication.readback',
              'publication.reconcile','publication.conflict'}




class PublicationComponent:
    name = 'publication'

    def __init__(self, deps: Mapping[str, object]):
        self.store = deps.get('store')
        self.authority = deps.get('authorization')
        self.clock = deps.get('clock_us', lambda: time.time_ns() // 1000)
        self.providers = {}
        if deps.get('github_transport') is not None:
            self.providers['github'] = GitHubAdapter(deps['github_transport'])
        if deps.get('drive_transport') is not None and deps.get('artifact_reader') is not None:
            self.providers['drive'] = DriveAdapter(deps['drive_transport'],
                                 deps['artifact_reader'], deps.get('max_artifact_bytes', 16 * 1024 * 1024))
        quotas = deps.get('quotas', {})
        self.quotas = {}
        for provider in ('github','drive'):
            q = quotas.get(provider) if isinstance(quotas, Mapping) else None
            if isinstance(q, Mapping) and type(q.get('max_per_window')) is int and type(q.get('window_us')) is int \
               and q['max_per_window'] > 0 and q['window_us'] > 0:
                self.quotas[provider] = (q['max_per_window'], q['window_us'])
        self.max_attempts = deps.get('max_attempts', 8)
        self.backoff_us = deps.get('backoff_us', 1_000_000)

    def _now(self) -> int:
        return int(self.clock())

    def _event(self, tx: object, identity: VerifiedIdentity, command: CommandEnvelope,
               kind: str, intent: str, data: Mapping[str, Any] | None = None) -> str:
        now = self._now()
        event = DomainEvent(event_id=str(uuid.uuid4()), domain='publication', kind=kind,
            actor_principal_id=identity.principal_id, issued_at_us=now,
            correlation_id=command.request_id, causation_id=intent,
            job_id=command.job_id, shift_id=command.shift_id,
            assignment_epoch=command.assignment_epoch,
            payload_sha256=digest(canonical(data or {'intent_id':intent,'kind':kind}).encode()),
            payload=dict(data or {'intent_id':intent,'kind':kind}))
        return tx.append_event(event)

    def _permission(self, identity: VerifiedIdentity, operation: str, bindings: Mapping[str, Any]) -> Outcome | None:
        if not isinstance(identity, VerifiedIdentity) or not identity.principal_id or not identity.credential_id \
           or not identity.issuer_id or not identity.authenticated_via or identity.expires_at_us <= self._now():
            return Outcome('DENIED','IDENTITY_UNVERIFIED')
        if operation not in identity.scopes:
            return Outcome('DENIED','INVALID_SCOPE')
        if self.authority is None:
            return Outcome('BLOCKED','NOT_CONFIGURED')
        try:
            # A trusted composition-root/WG-B Component must verify LIVE epoch,
            # issuer, source/lease/QA and owner/head approval. The binding is
            # a closed service-to-service trust.authorize query, not a custom
            # private sister-module method and never a prompt claim.
            auth_query = QueryEnvelope(version='NOMAD/QUERY/1',request_id=str(uuid.uuid4()),
                domain='trust',operation='trust.authorize',
                payload={'resource':'publication','operation':operation,
                         'bindings':dict(bindings),
                         'bindings_sha256':fingerprint(dict(bindings))})
            granted = self.authority.query(auth_query,identity)
            if not isinstance(granted,Outcome) or granted.state != 'ACCEPTED' or granted.code != 'AUTHORIZED':
                return Outcome('DENIED','REVOKED_GRANT')
        except Exception:
            return Outcome('BLOCKED','PROVIDER_UNKNOWN')
        return None

    def _configuration(self, provider: str | None = None) -> Outcome | None:
        if self.store is None: return Outcome('BLOCKED','NOT_CONFIGURED')
        if provider is not None and (provider not in self.providers or provider not in self.quotas):
            return Outcome('BLOCKED','NOT_CONFIGURED')
        return None

    @staticmethod
    def _out(row: Mapping[str, Any]) -> Outcome:
        state = row['state']
        result_state = ('ACCEPTED' if state in {'PREPARED','DISPATCHING','EXTERNALLY_OBSERVED','VERIFIED'}
                        else 'CONFLICT' if state == 'CONFLICT' else 'UNKNOWN' if state == 'PENDING_UNKNOWN'
                        else 'BLOCKED')
        return Outcome(result_state, state, row['intent_id'],
                       {'intent_id':row['intent_id'], 'status':state,
                        'attempts':row['attempt_count'], 'observed_revision':row['observed_revision'],
                        'next_attempt_us':row['next_attempt_us']})

    def _load(self, intent_id: str) -> Mapping[str, Any] | None:
        return self.store.read(lambda r:r.fetch_one('SELECT * FROM publication_intents WHERE intent_id=?',(intent_id,)))

    def _prepare(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        p = validate_prepare(command.payload)
        b = p['bindings']
        if command.job_id != b['job_id'] or command.assignment_epoch != b['assignment_epoch'] \
           or command.source_sha != b['source_sha']:
            return Outcome('DENIED','STALE_ASSIGNMENT')
        blocked = self._configuration(p['provider']) or self._permission(identity,'publication.prepare',b)
        if blocked: return blocked
        fp = fingerprint(p)
        now = self._now()
        intent_id = str(uuid.uuid4())

        def reserve(tx: object) -> Outcome:
            prior = tx.fetch_one('SELECT * FROM publication_intents WHERE idempotency_key=? OR effect_key=?',
                                 (command.idempotency_key,p['effect_key']))
            if prior:
                if prior['fingerprint'] != fp or prior['principal_id'] != identity.principal_id \
                   or prior['idempotency_key'] != command.idempotency_key or prior['effect_key'] != p['effect_key']:
                    return Outcome('CONFLICT','IDEMPOTENCY_CONFLICT')
                return self._out(prior)
            tx.execute('INSERT INTO publication_intents (intent_id,idempotency_key,effect_key,fingerprint,principal_id,'
                'provider,action,payload_json,state,created_us,updated_us) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (intent_id,command.idempotency_key,p['effect_key'],fp,identity.principal_id,
                 p['provider'],p['action'],canonical(p),'PREPARED',now,now))
            audit = self._event(tx,identity,command,'PREPARED',intent_id,
                                {'intent_id':intent_id,'effect_key_sha256':digest(p['effect_key'].encode()),
                                 'artifact_sha256':p['artifact_sha256'], 'provider':p['provider']})
            return Outcome('ACCEPTED','PREPARED',audit,{'intent_id':intent_id,'status':'PREPARED'})
        return self.store.transact(reserve)

    def _get_authorized(self, command: CommandEnvelope, identity: VerifiedIdentity, action: str):
        payload = fields(command.payload, {'intent_id'})
        intent_id = ident(payload['intent_id'])
        row = self._load(intent_id)
        if row is None: return None, None, Outcome('DENIED','INVALID_SCHEMA')
        p = json.loads(row['payload_json'])
        if row['principal_id'] != identity.principal_id and 'publication.recover' not in identity.scopes:
            return None, None, Outcome('DENIED','INVALID_SCOPE')
        blocked = self._configuration(p['provider']) or self._permission(identity,action,p['bindings'])
        if blocked: return None, None, blocked
        if command.job_id is not None and command.job_id != p['bindings']['job_id']:
            return None,None,Outcome('DENIED','STALE_ASSIGNMENT')
        if command.assignment_epoch is not None and command.assignment_epoch != p['bindings']['assignment_epoch']:
            return None,None,Outcome('DENIED','STALE_ASSIGNMENT')
        return row, p, None

    def _quota_reserve(self, tx: object, provider: str, now: int) -> bool:
        maximum, window_us = self.quotas[provider]
        row = tx.fetch_one('SELECT * FROM publication_quota WHERE provider=?',(provider,))
        if row is None:
            tx.execute('INSERT INTO publication_quota(provider,window_start_us,used) VALUES(?,?,1)',(provider,now))
            return True
        if now >= row['window_start_us'] + window_us:
            tx.execute('UPDATE publication_quota SET window_start_us=?,used=1 WHERE provider=?',(now,provider))
            return True
        if row['used'] >= maximum: return False
        tx.execute('UPDATE publication_quota SET used=used+1 WHERE provider=?',(provider,))
        return True

    def _dispatch(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        row,p,error = self._get_authorized(command,identity,'publication.dispatch')
        if error: return error
        if row['state'] != 'PREPARED': return self._out(row)
        now = self._now()
        claim = str(uuid.uuid4())

        def claim_tx(tx: object) -> Outcome:
            current = tx.fetch_one('SELECT * FROM publication_intents WHERE intent_id=?',(row['intent_id'],))
            if current['state'] != 'PREPARED': return self._out(current)
            if now < current['next_attempt_us']: return Outcome('BLOCKED','BACKPRESSURE',
                                            payload={'next_attempt_us':current['next_attempt_us']})
            if current['attempt_count'] >= self.max_attempts:
                tx.execute('UPDATE publication_intents SET state=?,last_code=?,updated_us=? WHERE intent_id=?',
                           ('BLOCKED','ATTEMPT_LIMIT',now,row['intent_id']))
                self._event(tx,identity,command,'BLOCKED',row['intent_id'])
                return Outcome('BLOCKED','BACKPRESSURE')
            if not self._quota_reserve(tx,p['provider'],now): return Outcome('BLOCKED','BACKPRESSURE')
            changed = tx.execute('UPDATE publication_intents SET state=?,attempt_count=attempt_count+1,'
                 'claim_token=?,last_code=?,updated_us=? WHERE intent_id=? AND state=?',
                 ('DISPATCHING',claim,'DISPATCHING',now,row['intent_id'],'PREPARED'))
            if changed != 1: return Outcome('CONFLICT','STALE_LEASE')
            self._event(tx,identity,command,'DISPATCHING',row['intent_id'])
            return Outcome('ACCEPTED','DISPATCHING',payload={'intent_id':row['intent_id']})
        claimed = self.store.transact(claim_tx)
        if claimed.code != 'DISPATCHING': return claimed

        # All external side effects are strictly outside StateStore.transact.
        try:
            self.providers[p['provider']].publish(p)
        except ProviderConflict:
            return self._finish_dispatch(row['intent_id'],claim,command,identity,'CONFLICT','EXPECTED_REVISION_CONFLICT')
        except ProviderForbidden:
            return self._finish_dispatch(row['intent_id'],claim,command,identity,'BLOCKED','NOT_SUPPORTED')
        except ProviderRateLimited as exc:
            return self._rate_limited(row['intent_id'],claim,command,identity,exc.retry_after_us)
        except Exception:
            return self._finish_dispatch(row['intent_id'],claim,command,identity,'PENDING_UNKNOWN','PROVIDER_UNKNOWN')
        # Receipt is never authoritative. A separate readback must verify bytes.
        observed = self._finish_dispatch(row['intent_id'],claim,command,identity,
                                          'EXTERNALLY_OBSERVED','READBACK_REQUIRED')
        if observed.code != 'EXTERNALLY_OBSERVED': return observed
        return self._readback_internal(row['intent_id'],p,command,identity)

    def _finish_dispatch(self, intent_id: str, claim: str, command: CommandEnvelope,
                         identity: VerifiedIdentity, state: str, code: str) -> Outcome:
        now = self._now()
        def txfn(tx: object) -> Outcome:
            n = tx.execute('UPDATE publication_intents SET state=?,last_code=?,claim_token=NULL,updated_us=? '
                           'WHERE intent_id=? AND state=? AND claim_token=?',
                           (state,code,now,intent_id,'DISPATCHING',claim))
            if n != 1: return Outcome('CONFLICT','STALE_LEASE')
            if state == 'CONFLICT':
                tx.execute('INSERT OR IGNORE INTO publication_dead_letters(intent_id,reason,created_us) VALUES (?,?,?)',
                           (intent_id,code,now))
            audit = self._event(tx,identity,command,state,intent_id)
            return Outcome('UNKNOWN' if state == 'PENDING_UNKNOWN' else 'CONFLICT' if state == 'CONFLICT'
                           else 'BLOCKED' if state == 'BLOCKED' else 'ACCEPTED',
                           code if state != 'EXTERNALLY_OBSERVED' else state,audit,
                           {'intent_id':intent_id,'status':state})
        return self.store.transact(txfn)

    def _rate_limited(self, intent_id: str, claim: str, command: CommandEnvelope,
                      identity: VerifiedIdentity, retry_after_us: int) -> Outcome:
        now = self._now()
        def txfn(tx: object) -> Outcome:
            row = tx.fetch_one('SELECT attempt_count FROM publication_intents WHERE intent_id=?',(intent_id,))
            delay = min(3_600_000_000, max(1, self.backoff_us) * 2 ** min(row['attempt_count']-1,16))
            if type(retry_after_us) is int and retry_after_us > 0:
                delay = max(delay, min(retry_after_us,3_600_000_000))
            n = tx.execute('UPDATE publication_intents SET state=?,last_code=?,claim_token=NULL,'
                           'next_attempt_us=?,updated_us=? WHERE intent_id=? AND state=? AND claim_token=?',
                           ('PREPARED','BACKPRESSURE',now+delay,now,intent_id,'DISPATCHING',claim))
            if n != 1: return Outcome('CONFLICT','STALE_LEASE')
            self._event(tx,identity,command,'RATE_LIMITED',intent_id)
            return Outcome('BLOCKED','BACKPRESSURE',payload={'intent_id':intent_id,'next_attempt_us':now+delay})
        return self.store.transact(txfn)

    def _readback_internal(self, intent_id: str, p: dict, command: CommandEnvelope,
                           identity: VerifiedIdentity) -> Outcome:
        try:
            observation = self.providers[p['provider']].observe(p)
            if not isinstance(observation, Observation) or observation.kind not in {
                    'APPLIED','ABSENT_SAFE','ABSENT_UNSAFE','CONFLICT','UNKNOWN'}:
                observation = Observation('UNKNOWN')
        except Exception:
            observation = Observation('UNKNOWN')
        now = self._now()
        def update(tx: object) -> Outcome:
            old = tx.fetch_one('SELECT * FROM publication_intents WHERE intent_id=?',(intent_id,))
            if old['state'] in ('VERIFIED','CONFLICT','BLOCKED'): return self._out(old)
            if observation.kind == 'APPLIED': state,code = 'VERIFIED','VERIFIED'
            elif observation.kind == 'CONFLICT': state,code = 'CONFLICT','DOCUMENT_CONFLICT'
            elif observation.kind == 'ABSENT_SAFE' and p['action'] != 'create_pr':
                state,code = 'PREPARED','ABSENT_SAFE'
            else: state,code = 'PENDING_UNKNOWN','PROVIDER_UNKNOWN'
            tx.execute('UPDATE publication_intents SET state=?,observed_revision=?,last_code=?,'
                       'claim_token=NULL,updated_us=? WHERE intent_id=?',
                       (state,observation.revision,code,now,intent_id))
            if state == 'CONFLICT':
                tx.execute('INSERT OR IGNORE INTO publication_dead_letters(intent_id,reason,created_us) VALUES (?,?,?)',
                           (intent_id,code,now))
            if state != old['state'] or observation.kind == 'APPLIED':
                audit = self._event(tx,identity,command,state,intent_id,
                                   {'intent_id':intent_id,'observation':observation.kind,
                                    'revision':observation.revision})
            else: audit = None
            return Outcome('ACCEPTED' if state in ('VERIFIED','PREPARED') else
                           'CONFLICT' if state == 'CONFLICT' else 'UNKNOWN',code,audit,
                           {'intent_id':intent_id,'status':state,'observed_revision':observation.revision})
        return self.store.transact(update)

    def _readback(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        row,p,error = self._get_authorized(command,identity,command.operation)
        if error: return error
        return self._readback_internal(row['intent_id'],p,command,identity)

    def _conflict(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        data = fields(command.payload, {'intent_id','reason'})
        ident(data['intent_id'])
        reason = ident(data['reason'])
        row,p,error = self._get_authorized(
            CommandEnvelope(**{k:getattr(command,k) for k in command.__dataclass_fields__ if k!='payload'},
                            payload={'intent_id':data['intent_id']}), identity, 'publication.conflict')
        if error: return error
        now=self._now()
        def txn(tx: object) -> Outcome:
            old = tx.fetch_one('SELECT * FROM publication_intents WHERE intent_id=?',(row['intent_id'],))
            if old['state']=='VERIFIED': return Outcome('DENIED','INVALID_SCOPE')
            if old['state']=='CONFLICT': return self._out(old)
            tx.execute('UPDATE publication_intents SET state=?,last_code=?,claim_token=NULL,updated_us=? WHERE intent_id=?',
                       ('CONFLICT',reason,now,row['intent_id']))
            tx.execute('INSERT OR IGNORE INTO publication_dead_letters(intent_id,reason,created_us) VALUES(?,?,?)',
                       (row['intent_id'],reason,now))
            audit=self._event(tx,identity,command,'CONFLICT',row['intent_id'])
            return Outcome('CONFLICT','DOCUMENT_CONFLICT',audit,{'intent_id':row['intent_id']})
        return self.store.transact(txn)

    def execute(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        try:
            if not isinstance(command,CommandEnvelope) or command.version!='NOMAD/CMD/1' \
               or command.domain!='publication' or command.operation not in OPERATIONS \
               or not isinstance(command.request_id,str) or not 1 <= len(command.request_id) <= 160 \
               or not isinstance(command.idempotency_key,str) or not 1 <= len(command.idempotency_key) <= 160:
                return Outcome('DENIED','MALFORMED_REQUEST')
            if command.operation == 'publication.prepare': return self._prepare(command,identity)
            if command.operation == 'publication.dispatch': return self._dispatch(command,identity)
            if command.operation in ('publication.readback','publication.reconcile'):
                return self._readback(command,identity)
            return self._conflict(command,identity)
        except (ValidationError,ValueError,TypeError):
            return Outcome('DENIED','INVALID_SCHEMA')
        except Exception:
            # Never print provider credential or SQL exception text into a client receipt.
            return Outcome('BLOCKED','PROVIDER_UNKNOWN')

    def query(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        if not isinstance(query,QueryEnvelope) or query.version!='NOMAD/QUERY/1' \
           or query.domain!='publication' or query.operation not in ('publication.status','publication.pending') \
           or type(query.limit) is not int or not 1 <= query.limit <= 100:
            return Outcome('DENIED','MALFORMED_REQUEST')
        try:
            if query.operation == 'publication.pending':
                fields(query.payload,set())
            else:
                fields(query.payload,set(),{'intent_id'})
            permission = self._permission(identity,'publication.query',{})
            if permission: return permission
            config=self._configuration()
            if config: return config
            owner = 'publication.recover' in identity.scopes
            conditions, args = [], []
            if not owner:
                conditions.append('principal_id=?');args.append(identity.principal_id)
            if query.operation == 'publication.pending':
                conditions.append("state IN ('PREPARED','DISPATCHING','PENDING_UNKNOWN','EXTERNALLY_OBSERVED')")
            if 'intent_id' in query.payload:
                conditions.append('intent_id=?');args.append(ident(query.payload['intent_id']))
            if query.cursor is not None:
                conditions.append('intent_id>?');args.append(ident(query.cursor))
            where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
            rows=self.store.read(lambda r:r.fetch_all('SELECT * FROM publication_intents' + where +
                                        ' ORDER BY intent_id LIMIT ?',(*args,query.limit)))
            items=[{'intent_id':r['intent_id'], 'provider':r['provider'], 'action':r['action'],
                    'state':r['state'],'attempt_count':r['attempt_count'],
                    'observed_revision':r['observed_revision']} for r in rows]
            return Outcome('ACCEPTED','OK',payload={'items':items,
                         'next_cursor':rows[-1]['intent_id'] if len(rows)==query.limit else None})
        except (ValueError,ValidationError): return Outcome('DENIED','INVALID_SCHEMA')
        except Exception: return Outcome('BLOCKED','PROVIDER_UNKNOWN')


def build_component(deps: Mapping[str, object]) -> PublicationComponent:
    return PublicationComponent(deps)
