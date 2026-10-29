"""R9 EffectPort: durable idempotent intents; provider IO outside writer transaction.

P2 supplies an authenticated provider capable of native receipt observation.
Simulated responses are not success evidence and never commit an effect.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from nomad_studio.production.context.core import (
    Result, Decision, Invalid, check_id, check_sha, check_lineage, digest,
    failure, json_bytes, row_one, run_record, store_bytes, verified_bytes,
)

_REQUIRED = {'effect_id', 'project_id', 'feature_id', 'action', 'target_ref',
             'bible_pair_digest', 'feature_contract_digest', 'scope_digest',
             'payload_digest', 'idempotency_key'}
_ACTIONS = {'FEATURE_BRANCH_WRITE', 'FEATURE_BRANCH_COMMIT', 'PROTECTED_MAINLINE_MERGE',
            'RELEASE_PUBLISH', 'BIBLE_ACTIVATE', 'BUDGET_EXPAND', 'EXTERNAL_UNSCOPED_WRITE'}
_PROTECTED = _ACTIONS - {'FEATURE_BRANCH_WRITE', 'FEATURE_BRANCH_COMMIT'}


class EffectService:
    def __init__(self, db: Any, cas: Any, provider: Any, scope: Any,
                 owner_auth: Any, clock_us: Any):
        self.db, self.cas, self.provider, self.scope = db, cas, provider, scope
        self.owner_auth, self.clock_us = owner_auth, clock_us

    def _intent(self, effect_id: str) -> tuple[Mapping, dict, str]:
        row = row_one(self.db, 'SELECT * FROM v1_effect_intents WHERE effect_id=?', (effect_id,))
        if row is None:
            raise Invalid('NOT_FOUND', 'effect was not prepared')
        archive = row_one(self.db, 'SELECT * FROM v1_archive_refs WHERE archive_id=?', ('effect-' + effect_id,))
        if archive is None:
            raise Invalid('EVIDENCE_INVALID', 'effect intent bytes unavailable')
        intent = json.loads(verified_bytes(self.cas, archive['storage_ref'], archive['content_digest']))
        if (intent['effect_id'] != effect_id or row['payload_digest'] != intent['payload_digest']
                or row['scope_digest'] != intent['scope_digest']
                or row['action'] != intent['action'] or row['target_ref'] != intent['target_ref']):
            raise Invalid('EVIDENCE_INVALID', 'effect intent storage corruption')
        return row, intent, archive['content_digest']

    def prepare_effect(self, intent: Mapping, scope_decision: Decision,
                       provider_permission: Decision) -> Result:
        try:
            if not isinstance(intent, Mapping) or not _REQUIRED <= set(intent) or set(intent) - _REQUIRED - {'expected_source_commit', 'owner_proof_ref'}:
                raise Invalid('INVALID_INPUT', 'effect intent schema error')
            item = dict(intent)
            for key in ('effect_id', 'project_id', 'feature_id', 'idempotency_key'):
                check_id(item[key])
            for key in ('bible_pair_digest', 'feature_contract_digest', 'scope_digest', 'payload_digest'):
                check_sha(item[key])
            run = run_record(self.db, item['feature_id'], item['project_id'])
            check_lineage(run, item)
            if item['action'] not in _ACTIONS:
                raise Invalid('INVALID_INPUT', 'unsupported effect action')
            if not isinstance(item['target_ref'], str) or not 0 < len(item['target_ref']) <= 16384:
                raise Invalid('INVALID_INPUT', 'invalid target_ref')
            if item['action'] in _PROTECTED:
                check_id(item.get('owner_proof_ref'))
                if not self.owner_auth.verify(item['owner_proof_ref'], item):
                    raise Invalid('OWNER_ACTION_REQUIRED', 'trusted owner proof missing')
            if item.get('expected_source_commit') is not None:
                check_sha(item['expected_source_commit'], commit=True)
            if any(getattr(x, 'allowed', None) is not True or getattr(x, 'status', None) != 'ALLOW' for x in
                   (scope_decision, provider_permission)):
                raise Invalid('PERMISSION_DENIED', 'effect or provider authorization denied')
            if not self.scope.permits(run, item):
                raise Invalid('SCOPE_EXCEEDED', 'scope no longer authorizes effect')
            provider_id = check_id(self.provider.provider_for(item['target_ref']))
            sha, ref = store_bytes(self.cas, json_bytes(item))
            now = int(self.clock_us())
            def insert(tx: Any) -> None:
                bound = tx.execute('SELECT * FROM v1_effect_idempotency WHERE provider_id=? AND idempotency_key=?',
                                   (provider_id, item['idempotency_key'])).fetchone()
                if bound is not None and bound['effect_id'] != item['effect_id']:
                    raise Invalid('IDEMPOTENCY_CONFLICT', 'provider key bound to different effect')
                old = tx.execute('SELECT * FROM v1_archive_refs WHERE archive_id=?',
                                 ('effect-' + item['effect_id'],)).fetchone()
                if old is not None:
                    if old['content_digest'] != sha:
                        raise Invalid('IDEMPOTENCY_CONFLICT', 'same effect id changed request intent')
                    if bound is None:
                        raise Invalid('EVIDENCE_INVALID', 'effect key binding missing')
                    return
                tx.execute('INSERT INTO v1_effect_intents '
                           '(feature_id,effect_id,action,scope_digest,payload_digest,state,target_ref,owner_decision_ref,created_at_us) '
                           'VALUES (?,?,?,?,?,?,?,?,?)',
                           (item['feature_id'], item['effect_id'], item['action'], item['scope_digest'],
                            item['payload_digest'], 'AUTHORIZED', item['target_ref'],
                            item.get('owner_proof_ref'), now))
                tx.execute('INSERT INTO v1_effect_idempotency (provider_id,idempotency_key,effect_id) VALUES (?,?,?)',
                           (provider_id, item['idempotency_key'], item['effect_id']))
                tx.execute('INSERT INTO v1_archive_refs (archive_id,feature_id,content_digest,storage_ref,created_at_us) '
                           'VALUES (?,?,?,?,?)', ('effect-' + item['effect_id'], item['feature_id'], sha, ref, now))
            self.db.write(insert)
            return Result('ACCEPTED', 'ACCEPTED_PENDING', {'effect_id': item['effect_id'],
                           'intent_digest': sha, 'provider_id': provider_id},
                          'effect-' + item['effect_id'], (ref,))
        except Invalid as exc:
            return failure(exc)

    def _commit_observation(self, row: Mapping, intent: Mapping, observation: Mapping) -> Result:
        provider_id = self.provider.provider_for(intent['target_ref'])
        if (not isinstance(observation, Mapping) or observation.get('simulated') is not False
                or observation.get('effect_id') != intent['effect_id']
                or observation.get('provider_id') != provider_id
                or observation.get('idempotency_key') != intent['idempotency_key']
                or observation.get('payload_digest') != intent['payload_digest']
                or not self.provider.verify_native_observation(observation)):
            return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
        if observation.get('applied') is False:
            def retry(tx: Any) -> None:
                tx.execute('UPDATE v1_effect_intents SET state=? WHERE effect_id=? AND state IN (?,?)',
                           ('AUTHORIZED', intent['effect_id'], 'UNKNOWN_EXTERNAL', 'DISPATCHED'))
            self.db.write(retry)
            return Result('RETRYABLE', 'LOCAL_RECOVERY_REQUIRED', {'effect_id': intent['effect_id']})
        if observation.get('applied') is not True:
            return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
        native_ref = check_id(observation.get('native_receipt_ref'))
        observed_digest = digest(dict(observation))
        receipt_id = 'receipt-' + digest({'effect': intent['effect_id'], 'provider': provider_id, 'ref': native_ref})
        def record(tx: Any) -> None:
            old = tx.execute('SELECT * FROM v1_provider_receipts WHERE provider_receipt_id=?', (receipt_id,)).fetchone()
            if old and old['evidence_digest'] != observed_digest:
                raise Invalid('IDEMPOTENCY_CONFLICT', 'same provider receipt changed evidence')
            if not old:
                tx.execute('INSERT INTO v1_provider_receipts '
                           '(provider_receipt_id,effect_id,provider_id,evidence_digest,observed_state,recorded_us) '
                           'VALUES (?,?,?,?,?,?)',
                           (receipt_id, intent['effect_id'], provider_id, observed_digest, 'APPLIED', int(self.clock_us())))
            tx.execute('UPDATE v1_effect_intents SET state=? WHERE effect_id=?', ('COMMITTED', intent['effect_id']))
        self.db.write(record)
        return Result('OK', 'OK', {'effect_id': intent['effect_id'], 'observed_digest': observed_digest},
                      receipt_id, (native_ref,))

    def dispatch_once(self, effect_id: str, idempotency_key: str) -> Result:
        try:
            check_id(effect_id)
            check_id(idempotency_key)
            row, intent, _ = self._intent(effect_id)
            provider_id = self.provider.provider_for(intent['target_ref'])
            bound = row_one(self.db, 'SELECT effect_id FROM v1_effect_idempotency WHERE provider_id=? AND idempotency_key=?',
                            (provider_id, idempotency_key))
            if not bound or bound['effect_id'] != effect_id or intent['idempotency_key'] != idempotency_key:
                raise Invalid('IDEMPOTENCY_CONFLICT', 'dispatch key does not match durable request')
            if row['state'] == 'COMMITTED':
                receipt = row_one(self.db, 'SELECT * FROM v1_provider_receipts WHERE effect_id=?', (effect_id,))
                if receipt is None:
                    raise Invalid('EVIDENCE_INVALID', 'committed effect has no provider receipt')
                return Result('OK', 'OK', {'effect_id': effect_id,
                    'observed_digest': receipt['evidence_digest']}, receipt['provider_receipt_id'])
            if row['state'] in ('UNKNOWN_EXTERNAL', 'DISPATCHED'):
                return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
            run = run_record(self.db, intent['feature_id'], intent['project_id'])
            check_lineage(run, intent)
            if not self.scope.permits(run, intent):
                raise Invalid('SCOPE_EXCEEDED', 'source/effect scope changed')
            if intent.get('expected_source_commit') and not self.provider.verify_source_fence(intent):
                raise Invalid('SOURCE_CONFLICT', 'source parent SHA changed before dispatch')
            def claim(tx: Any) -> bool:
                observed = tx.execute('SELECT state FROM v1_effect_intents WHERE effect_id=?', (effect_id,)).fetchone()
                if observed is None or observed['state'] != 'AUTHORIZED':
                    return False
                tx.execute('UPDATE v1_effect_intents SET state=? WHERE effect_id=?', ('DISPATCHED', effect_id))
                return True
            if not self.db.write(claim):
                return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
            try:
                self.provider.dispatch(intent, idempotency_key)
            except (TimeoutError, OSError):
                self.db.write(lambda tx: tx.execute('UPDATE v1_effect_intents SET state=? WHERE effect_id=?',
                                                      ('UNKNOWN_EXTERNAL', effect_id)))
                return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
            try:
                observed = self.provider.observe(intent)
                result = self._commit_observation(row, intent, observed)
            except (TimeoutError, OSError):
                result = Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
            if result.status == 'UNKNOWN_EXTERNAL':
                self.db.write(lambda tx: tx.execute('UPDATE v1_effect_intents SET state=? WHERE effect_id=?',
                                                     ('UNKNOWN_EXTERNAL', effect_id)))
            return result
        except Invalid as exc:
            return failure(exc)

    def reconcile_readback(self, effect_id: str, authoritative_provider_observation: Mapping) -> Result:
        try:
            check_id(effect_id)
            row, intent, _ = self._intent(effect_id)
            if not isinstance(authoritative_provider_observation, Mapping):
                raise Invalid('INVALID_INPUT', 'provider observation must be a record')
            observed = self.provider.observe(intent)
            if dict(authoritative_provider_observation) != dict(observed):
                raise Invalid('EVIDENCE_INVALID', 'submitted observation not independent provider readback')
            return self._commit_observation(row, intent, observed)
        except Invalid as exc:
            return failure(exc)
        except (TimeoutError, OSError):
            return Result('UNKNOWN_EXTERNAL', 'PROVIDER_UNKNOWN')
