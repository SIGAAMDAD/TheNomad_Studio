"""R9 WorkerHostPort and AgentExecutionPort. Only host-native readback is proof.

All external sends run outside the CAB-H SQLite table writer transaction. The
P2-owned effect journal is responsible for persistent deduplication/outbox;
unknown effects may be read back, but MUST NOT be blindly replayed.
"""
from __future__ import annotations
from typing import Any, Callable, Mapping, Sequence
from nomad_studio.trust.port_types import Result, Decision, canonical, digest, ident, sha, refused
from nomad_studio.trust.evidence import ProviderEvidenceVerifier, EvidenceError

CAPABILITIES = ('dispatch','checkpoint','same_session_reentry','fence','agent_execution','cancel')

def _native(event: Any, session: str | None = None) -> bool:
    c = event.claims
    return (event.production and c.get('observed_native') is True and
            c.get('source') == 'HOST_NATIVE' and c.get('simulated') is False and
            (session is None or c.get('session_id') == session) and
            type(c.get('observed_at_us')) is int and c['observed_at_us'] > 0)

class WorkerHostService:
    def __init__(self, *, store: Any, verifier: ProviderEvidenceVerifier,
                 host: Any, journal: Any, task_authority: Any, clock_us: Callable[[], int]):
        self.store, self.verifier, self.host = store, verifier, host
        self.journal, self.task_authority, self.clock_us = journal, task_authority, clock_us

    def _read(self, ref: str, kind: str, bindings: Mapping[str, Any]):
        ev = self.verifier.verify(ref, kind, bindings=bindings)
        if not _native(ev):
            raise EvidenceError('not real host-native evidence')
        if ev.claims['observed_at_us'] > self.clock_us():
            raise EvidenceError('future-dated native receipt')
        return ev

    def get_capability_manifest(self, provider_id: str) -> Result:
        if not ident(provider_id):
            return refused('INVALID_INPUT')
        empty = {'provider_id':provider_id,'capabilities':{k:'NOT_VERIFIED' for k in CAPABILITIES},
                 'actual_host_observed':False}
        if self.host is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED',record=empty)
        try:
            ref = self.host.manifest_ref(provider_id)
            if not ident(ref):
                return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED',record=empty)
            ev = self._read(ref,'HOST_MANIFEST',{'provider_id':provider_id})
            raw = ev.claims.get('capabilities')
            if not isinstance(raw,Mapping) or set(raw) != set(CAPABILITIES) or any(
                value not in ('SUPPORTED','UNSUPPORTED','NOT_VERIFIED') for value in raw.values()):
                return refused('EVIDENCE_INVALID')
            caps = dict(raw)
            def save(tx):
                for name,state in caps.items():
                    tx.execute('INSERT INTO v1_provider_capabilities(provider_id,capability_name,verified_state,evidence_ref) '
                        'VALUES (?,?,?,?) ON CONFLICT(provider_id,capability_name) DO UPDATE SET '
                        'verified_state=excluded.verified_state,evidence_ref=excluded.evidence_ref',
                        (provider_id,name,state,ref))
            self.store.transaction(save)
            return Result('OK','OK',record={'provider_id':provider_id,
                'capabilities':caps,'actual_host_observed':True},receipt_ref=ref,evidence_refs=(ref,))
        except EvidenceError:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED',record=empty)
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')

    def register_session(self, provider_proof_ref: str) -> Result:
        """Trusted ingress calls this internal method after native session admission."""
        try:
            ev = self._read(provider_proof_ref,'HOST_SESSION',{})
            c = ev.claims
            sid,principal = c.get('session_id'),c.get('authenticated_principal')
            epoch = c.get('session_epoch')
            if not ident(sid) or not ident(principal) or type(epoch) is not int or epoch < 0 or not digest(c.get('capability_digest')):
                return refused('EVIDENCE_INVALID')
            # Root's identity table is authoritative for native session principal.
            row = self.store.read_one('v1_verified_identities','principal_id=?', (principal,))
            if not row or row['valid_until_us'] <= self.clock_us():
                return refused('UNAUTHENTICATED')
            def save(tx):
                old = tx.fetch_one('SELECT * FROM v1_host_sessions WHERE session_id=?',(sid,))
                if old and (old['provider_id'] != ev.provider_id or
                            old['authenticated_principal'] != principal or old['session_epoch'] != epoch or
                            old['capability_digest'] != c['capability_digest'] or
                            old['observed_real'] != 1):
                    return False
                if not old:
                    tx.execute('INSERT INTO v1_host_sessions VALUES (?,?,?,?,?,?)',
                        (sid,ev.provider_id,principal,c['capability_digest'],1,epoch))
                return True
            if not self.store.transaction(save):
                return refused('LEASE_FENCED','CONFLICT')
            return Result('OK','OK',record={'session_id':sid,'provider_id':ev.provider_id,
                'authenticated_principal':principal,'session_epoch':epoch},
                receipt_ref=ev.ref,evidence_refs=(ev.ref,))
        except EvidenceError:
            return refused('EVIDENCE_INVALID')
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')

    def _session(self, sid: str) -> Mapping[str, Any] | None:
        return self.store.read_one('v1_host_sessions','session_id=?',(sid,))

    def _capability_state(self, provider_id: str, capability: str) -> str:
        cap = self.store.read_one('v1_provider_capabilities',
                                  'provider_id=? AND capability_name=?',(provider_id,capability))
        return str(cap['verified_state']) if cap and ident(cap['evidence_ref']) else 'NOT_VERIFIED'

    def _supported(self, provider_id: str, capability: str) -> bool:
        return self._capability_state(provider_id, capability) == 'SUPPORTED'

    def _capability_refusal(self, provider_id: str, capability: str) -> Result:
        if self._capability_state(provider_id, capability) == 'UNSUPPORTED':
            return refused('PROVIDER_UNSUPPORTED','UNSUPPORTED')
        return refused('PROVIDER_NOT_VERIFIED','NOT_VERIFIED')

    def observe_activation(self, session_id: str, distinct_job_ids: Sequence[str]) -> Result:
        if not ident(session_id) or type(distinct_job_ids) not in (tuple,list) or len(distinct_job_ids) < 2 or len(set(distinct_job_ids)) != len(distinct_job_ids) or any(not ident(j) for j in distinct_job_ids):
            return refused('INVALID_INPUT')
        if self.host is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            session = self._session(session_id)
            if not session or not session['observed_real']:
                return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
            if not self._supported(session['provider_id'],'same_session_reentry'):
                return self._capability_refusal(session['provider_id'],'same_session_reentry')
            refs = self.host.activation_history(session_id)
            if not isinstance(refs,(tuple,list)) or len(refs) > 4096:
                return refused('EVIDENCE_INVALID')
            events = []
            for ref in refs:
                if not ident(ref):
                    return refused('EVIDENCE_INVALID')
                # Purpose is obtained from independent native readback; no inferred activation.
                kind = self.host.receipt_kind(ref)
                if kind not in ('HOST_ACTIVATION','HOST_IDLE'):
                    return refused('EVIDENCE_INVALID')
                ev = self._read(ref,kind,{'session_id':session_id,
                    'session_epoch':session['session_epoch'],
                    'authenticated_principal':session['authenticated_principal']})
                if ev.provider_id != session['provider_id']:
                    return refused('EVIDENCE_INVALID')
                events.append(ev)
            # Exactly one authenticated session/epoch; strict native order and idle edge.
            events.sort(key=lambda e:e.claims['observed_at_us'])
            if len({e.claims['observed_at_us'] for e in events}) != len(events):
                return refused('EVIDENCE_INVALID')
            seen = []
            preceding_idle = None
            for ev in events:
                c=ev.claims
                if ev.purpose == 'HOST_IDLE':
                    preceding_idle = ev
                    continue
                job = c.get('job_id')
                if not ident(job) or not digest(c.get('checkpoint_digest')) or not ident(c.get('project_id')) or not ident(c.get('feature_id')):
                    return refused('EVIDENCE_INVALID')
                if not seen:
                    if c.get('after_idle_ref') is not None:
                        return refused('EVIDENCE_INVALID')
                else:
                    if (preceding_idle is None or
                        c.get('after_idle_ref') != preceding_idle.ref or
                        preceding_idle.claims['observed_at_us'] <= seen[-1].claims['observed_at_us'] or
                        c['observed_at_us'] <= preceding_idle.claims['observed_at_us']):
                        return refused('EVIDENCE_INVALID')
                if any(old.claims['job_id'] == job for old in seen):
                    return refused('EVIDENCE_INVALID')
                seen.append(ev)
                preceding_idle = None
            jobs = [e.claims['job_id'] for e in seen]
            if not all(j in jobs for j in distinct_job_ids):
                return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
            selected = [e for e in seen if e.claims['job_id'] in distinct_job_ids]
            if len({(e.claims['project_id'],e.claims['feature_id']) for e in selected}) != 1:
                return refused('WRONG_FEATURE','CONFLICT')
            if len(selected)<2:
                return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
            # Atomic CAB-H writes; duplicate native refs are immutable and replay-safe.
            def save(tx):
                for ev in events:
                    c=ev.claims
                    old = tx.fetch_one('SELECT observation_digest FROM v1_trusted_host_observations WHERE provider_native_ref=?',(ev.ref,))
                    h=sha(c)
                    if old and old['observation_digest']!=h:
                        return False
                    if not old:
                        tx.execute('INSERT INTO v1_trusted_host_observations VALUES (?,?,?,?,?)',
                            (sha({'session_id':session_id,'native_ref':ev.ref}), session_id,ev.ref,h,self.clock_us()))
                for ev in selected:
                    old=tx.fetch_one('SELECT host_native_receipt_ref FROM v1_host_activations WHERE session_id=? AND job_id=?',
                        (session_id,ev.claims['job_id']))
                    if old and old['host_native_receipt_ref'] != ev.ref:
                        return False
                    if not old:
                        tx.execute('INSERT INTO v1_host_activations VALUES (?,?,?,?)',
                            (session_id,ev.claims['job_id'],ev.ref,ev.claims['observed_at_us']))
                return True
            if not self.store.transaction(save):
                return refused('EVIDENCE_INVALID','CONFLICT')
            last=selected[-1]; c=last.claims
            record={'project_id':c['project_id'],'feature_id':c['feature_id'],
                'job_id':c['job_id'],'session_id':session_id,'provider_id':last.provider_id,
                'host_native_receipt_ref':last.ref,'authenticated_principal':c['authenticated_principal'],
                'actual_host_observed':True,'checkpoint_digest':c['checkpoint_digest'],
                'recorded_at_us':c['observed_at_us']}
            return Result('OK','OK',record=record,receipt_ref=last.ref,
                evidence_refs=tuple(e.ref for e in events))
        except EvidenceError:
            return refused('EVIDENCE_INVALID')
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')

    def _external(self, *, key: str, payload: Mapping[str, Any], kind: str,
                  sender: Callable[[], Any], reader: Callable[[], Any],
                  bindings: Mapping[str, Any]) -> Result:
        """P2's durable journal admits unique digest; never retry uncertain sends."""
        d=sha(payload)
        try:
            disposition = self.journal.reserve(kind,key,d)
            if disposition == 'CONFLICT':
                return refused('IDEMPOTENCY_CONFLICT','CONFLICT')
            if disposition not in ('NEW','SAME'):
                return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')
            if disposition == 'NEW':
                try:
                    sender()
                except Exception:
                    self.journal.mark_unknown(kind,key,d)
                    return refused('PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
            # No second provider send for SAME, even after UNKNOWN; readback only.
            ref=reader()
            if not ident(ref):
                self.journal.mark_unknown(kind,key,d)
                return refused('PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
            ev=self._read(ref,kind,bindings)
            if ev.claims.get('request_digest') != d or ev.claims.get('effect_state') != 'APPLIED':
                self.journal.mark_unknown(kind,key,d)
                return refused('PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
            try:
                self.journal.mark_verified(kind,key,d,ref)
            except Exception:
                try: self.journal.mark_unknown(kind,key,d)
                except Exception: pass
                return refused('PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
            return Result('ACCEPTED','OK',record={'request_digest':d,'provider_receipt_ref':ref},
                          receipt_ref=ref,evidence_refs=(ref,))
        except EvidenceError:
            try: self.journal.mark_unknown(kind,key,d)
            except Exception: pass
            return refused('PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')

    def dispatch_task(self, task: Mapping[str, Any], worker_session_ref: str,
                      context_bundle_digest: str) -> Result:
        # Task is EXACT R8 TaskSpec; runtime session/lease facts are not task fields.
        required = {'project_id','feature_id','task_id','graph_revision','cabal_id',
            'bible_pair_digest','feature_contract_digest','dependencies','required_skills',
            'owned_resources','input_digests','outputs','acceptance_ids','budget_limit_units','task_digest'}
        if (not isinstance(task,Mapping) or set(task) != required or
            not ident(worker_session_ref) or not digest(context_bundle_digest) or
            any(not ident(task.get(k)) for k in ('project_id','feature_id','task_id','cabal_id')) or
            any(not digest(task.get(k)) for k in
                ('bible_pair_digest','feature_contract_digest','task_digest')) or
            type(task.get('graph_revision')) is not int or task['graph_revision'] < 1 or
            type(task.get('budget_limit_units')) is not int or task['budget_limit_units'] < 0 or
            any(type(task.get(k)) is not list or len(task[k]) != len(set(map(str,task[k]))) or
                any(not (digest(v) if k=='input_digests' else ident(v)) for v in task[k])
                for k in ('dependencies','required_skills','owned_resources',
                          'input_digests','outputs','acceptance_ids'))):
            return refused('INVALID_INPUT')
        if self.host is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            session_proof=self._read(worker_session_ref,'HOST_SESSION',{})
            sid = session_proof.claims.get('session_id')
            if not ident(sid):
                return refused('EVIDENCE_INVALID')
            sess=self._session(sid)
            if (not sess or sess['authenticated_principal'] != session_proof.claims.get('authenticated_principal') or
                sess['provider_id'] != session_proof.provider_id or
                sess['session_epoch'] != session_proof.claims.get('session_epoch') or
                not sess['observed_real']):
                return refused('LEASE_FENCED','CONFLICT')
            # CAB-C owns lease/feature/graph/bible fencing and returns typed
            # current session+lease facts. No task-supplied lease epoch is trusted.
            permission=self.task_authority.verify_dispatch(dict(task),sess['authenticated_principal'],sid)
            if (not isinstance(permission,Mapping) or permission.get('authorized') is not True or
                type(permission.get('lease_epoch')) is not int or permission['lease_epoch'] < 0 or
                permission.get('session_epoch') != sess['session_epoch'] or
                permission.get('task_id') != task['task_id']):
                return refused('PERMISSION_DENIED')
            if not self._supported(sess['provider_id'],'dispatch'):
                return self._capability_refusal(sess['provider_id'],'dispatch')
            payload={'task':dict(task),'session_id':sid,
                     'worker_principal_id':sess['authenticated_principal'],
                     'session_epoch':sess['session_epoch'],
                     'lease_epoch':permission['lease_epoch'],
                     'context_bundle_digest':context_bundle_digest}
            key=f"{task['task_id']}:{permission['lease_epoch']}:{sess['session_epoch']}"
            return self._external(key=key,payload=payload,kind='HOST_DISPATCH',
                sender=lambda:self.host.dispatch(dict(payload),key),
                reader=lambda:self.host.lookup_effect('HOST_DISPATCH',key),
                bindings={'session_id':sid,'task_id':task['task_id'],
                          'job_id':task['task_id'],'provider_id':sess['provider_id']})
        except EvidenceError:
            return refused('UNAUTHENTICATED')
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')

    def read_checkpoint(self, session_id: str, job_id: str, attempt_epoch: int) -> Result:
        if not ident(session_id) or not ident(job_id) or type(attempt_epoch) is not int or attempt_epoch<0:
            return refused('INVALID_INPUT')
        if self.host is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            s=self._session(session_id)
            if not s or not self._supported(s['provider_id'],'checkpoint'):
                return self._capability_refusal(s['provider_id'],'checkpoint')
            ref=self.host.checkpoint_ref(session_id,job_id,attempt_epoch)
            if not ident(ref):
                return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
            ev=self._read(ref,'HOST_CHECKPOINT',{'session_id':session_id,'job_id':job_id,
                'attempt_epoch':attempt_epoch,'authenticated_principal':s['authenticated_principal']})
            if not digest(ev.claims.get('checkpoint_digest')) or ev.provider_id!=s['provider_id']:
                return refused('EVIDENCE_INVALID')
            return Result('OK','OK',record={'session_id':session_id,'job_id':job_id,
                'attempt_epoch':attempt_epoch,'checkpoint_digest':ev.claims['checkpoint_digest']},
                receipt_ref=ev.ref,evidence_refs=(ev.ref,))
        except EvidenceError:
            return refused('EVIDENCE_INVALID')
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')

    def fence_session(self, session_id: str, expected_epoch: int) -> Result:
        if not ident(session_id) or type(expected_epoch) is not int or expected_epoch<0:
            return refused('INVALID_INPUT')
        if self.host is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            s=self._session(session_id)
            if not s:
                return refused('NOT_FOUND','NOT_FOUND')
            if s['session_epoch'] != expected_epoch:
                return refused('LEASE_FENCED','CONFLICT')
            if not self._supported(s['provider_id'],'fence'):
                return self._capability_refusal(s['provider_id'],'fence')
            payload={'session_id':session_id,'session_epoch':expected_epoch,'provider_id':s['provider_id']}
            key=f'{session_id}:{expected_epoch}'
            outcome=self._external(key=key,payload=payload,kind='HOST_FENCE',
                sender=lambda:self.host.fence_session(session_id,expected_epoch,key),
                reader=lambda:self.host.lookup_effect('HOST_FENCE',key),bindings=payload)
            if outcome.status != 'ACCEPTED':
                return outcome
            def fence(tx):
                row=tx.fetch_one('SELECT session_epoch FROM v1_host_sessions WHERE session_id=?',(session_id,))
                if not row or row['session_epoch'] != expected_epoch:
                    return False
                tx.execute('UPDATE v1_host_sessions SET session_epoch=session_epoch+1,observed_real=0 WHERE session_id=? AND session_epoch=?',
                           (session_id,expected_epoch))
                return True
            if not self.store.transaction(fence):
                return refused('LEASE_FENCED','CONFLICT')
            return outcome
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')

class AgentExecutionService:
    def __init__(self, host_service: WorkerHostService, *, authority: Any):
        self.host_service,self.authority=host_service,authority

    def start_agent_job(self, job_spec: Mapping[str, Any], context_bundle_digest: str,
                        required_capabilities: Sequence[str]) -> Result:
        if (not isinstance(job_spec,Mapping) or set(job_spec) != {'task','host_session_ref'} or
            not ident(job_spec.get('host_session_ref')) or not digest(context_bundle_digest) or
            not isinstance(required_capabilities,(list,tuple)) or
            any(not ident(c) for c in required_capabilities) or
            len(set(required_capabilities))!=len(required_capabilities)):
            return refused('INVALID_INPUT')
        try:
            if not self.authority.validate_agent_job(job_spec,required_capabilities):
                return refused('PERMISSION_DENIED')
            ev=self.host_service._read(job_spec['host_session_ref'],'HOST_SESSION',{})
            provider_id=ev.provider_id
            for cap in ('agent_execution',)+tuple(required_capabilities):
                if not self.host_service._supported(provider_id,cap):
                    return self.host_service._capability_refusal(provider_id,cap)
        except EvidenceError:
            return refused('UNAUTHENTICATED')
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')
        return self.host_service.dispatch_task(job_spec['task'],job_spec['host_session_ref'],context_bundle_digest)

    def observe_agent_result(self, job_id: str, attempt_epoch: int) -> Result:
        if not ident(job_id) or type(attempt_epoch) is not int or attempt_epoch<0:
            return refused('INVALID_INPUT')
        try:
            session=self.authority.session_for_job(job_id,attempt_epoch)
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')
        if not ident(session):
            return refused('NOT_FOUND','NOT_FOUND')
        return self.host_service.read_checkpoint(session,job_id,attempt_epoch)

    def request_agent_cancel(self, job_id: str, attempt_epoch: int) -> Result:
        if not ident(job_id) or type(attempt_epoch) is not int or attempt_epoch<0:
            return refused('INVALID_INPUT')
        try:
            sid=self.authority.session_for_job(job_id,attempt_epoch)
            if not ident(sid) or not self.authority.authorize_cancel(job_id,attempt_epoch):
                return refused('PERMISSION_DENIED')
            row=self.host_service._session(sid)
            if row is None:
                return refused('NOT_FOUND','NOT_FOUND')
            if not self.host_service._supported(row['provider_id'],'cancel'):
                return self.host_service._capability_refusal(row['provider_id'],'cancel')
            payload={'session_id':sid,'session_epoch':row['session_epoch'],
                'job_id':job_id,'attempt_epoch':attempt_epoch,'provider_id':row['provider_id']}
            key=f'{sid}:{job_id}:{attempt_epoch}:{row["session_epoch"]}'
            # CAB-C performs durable lease fencing independently; verified native
            # cancel acknowledgement never implies task completion or QA success.
            return self.host_service._external(key=key,payload=payload,kind='HOST_CANCEL',
                sender=lambda:self.host_service.host.cancel_job(job_id,attempt_epoch,key),
                reader=lambda:self.host_service.host.lookup_effect('HOST_CANCEL',key),
                bindings=payload)
        except Exception:
            return refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')
