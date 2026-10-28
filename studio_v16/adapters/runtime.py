"""Studio V1-to-V16 shadow projection and authenticated durable worker operations.

No implicit DB connection, auto-dispatch, external publisher or credential. All
commands must originate from an isolated gateway after verifying the worker;
legacy documents are journaled as observations, never interpreted as grants.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from studio_v16.db.store import GatewayContext, PostgresStore


class RuntimeBlocked(RuntimeError):
    pass


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _safe_id(value):
    if (not isinstance(value, str) or not value or '\\' in value or '..' in value
            or value.startswith('/') or value.endswith('/') or '//' in value
            or any(p in ('', '.', '..') for p in value.split('/')) or len(value)>180):
        raise RuntimeBlocked('INVALID_REFERENCE')
    return value


@dataclass(frozen=True)
class ShadowObservation:
    kind: str
    ref: str
    revision: str
    digest: str
    reconciliation: str


class DurableRuntime:
    """Caller passes a trusted service-authenticated identity, never JSON claims.

    SHADOW imports are *not* treated as authoritative state. The bridge sends
    authenticated DB commands unchanged to WG-C, which gates each mutation.
    """
    def __init__(self, store: PostgresStore, context_factory: Callable[[], GatewayContext]):
        self.store = store
        self._context_factory = context_factory

    def _context(self):
        ctx = self._context_factory()
        if not isinstance(ctx, GatewayContext) or not ctx.verified_gateway or not ctx.principal_id:
            raise RuntimeBlocked('IDENTITY_UNVERIFIED')
        return ctx

    def import_snapshot(self, kind, ref, revision, document):
        if kind not in ('GITHUB', 'DRIVE', 'LEDGER'):
            raise RuntimeBlocked('INVALID_SOURCE_KIND')
        _safe_id(ref)
        _safe_id(revision)
        if len(canonical_bytes(document)) > 8 * 1024 * 1024:
            raise RuntimeBlocked('SNAPSHOT_TOO_LARGE')
        import_id = 'shadow_' + hashlib.sha256(canonical_bytes([kind,ref,revision])).hexdigest()[:40]
        value = self.store.shadow_import(import_id, kind, ref, revision, document, self._context())
        return ShadowObservation(kind, ref, revision, value, 'UNVERIFIED')

    def compare_readback(self, item: ShadowObservation, verifier: Callable[[str,str,str], tuple[dict,str]]):
        """Verifier is trusted/in-process and supplies independently read external JSON.

        Comparison uses content and exact source revision; a moved revision fails
        even when content is otherwise identical. Mismatch is persisted in WG-C.
        """
        value, revision = verifier(item.kind, item.ref, item.revision)
        if revision != item.revision:
            raise RuntimeBlocked('SOURCE_REVISION_DRIFT')
        context = self._context()
        if not context.provider_readback_verified:
            raise RuntimeBlocked('READBACK_NOT_VERIFIED')
        result = self.store.reconcile_shadow(item.kind,item.ref,item.revision,digest(value),context)
        return ShadowObservation(item.kind,item.ref,item.revision,item.digest,result)

    def observe_legacy_runtime(self, root: Path, revision: str):
        """Read-only import of complete V1 state and communications surfaces."""
        root = Path(root)
        observations = []
        for filename in ('jobs.json','slots.json','config.json','features.json','chat_workers.json'):
            path = root/'studio'/filename
            if not path.is_file() or path.is_symlink():
                continue
            raw = path.read_bytes()
            if len(raw) > 8*1024*1024:
                raise RuntimeBlocked('SNAPSHOT_TOO_LARGE')
            document = json.loads(raw)
            observations.append(self.import_snapshot('LEDGER','studio/'+filename,revision,document))
        return observations

    def observe_archived_events(self, comms_root: Path, revision: str, max_events=5000):
        """Bounded append-only V1 event observations; no inbox reauthorization."""
        base = Path(comms_root) / 'events'
        files = sorted(base.glob('*/*/*/evt-*.json'))
        if len(files) > max_events or max_events > 10000 or max_events < 1:
            raise RuntimeBlocked('EVENT_LIMIT_EXCEEDED')
        receipts = []
        for event in files:
            if event.is_symlink() or not event.is_file():
                raise RuntimeBlocked('INVALID_ARCHIVED_EVENT')
            raw = event.read_bytes()
            if len(raw) > 1024 * 1024:
                raise RuntimeBlocked('EVENT_TOO_LARGE')
            record = json.loads(raw)
            if (not isinstance(record, dict) or
                    record.get('delivery_authority') != 'ARCHIVE_ONLY' or
                    event.stem != record.get('event_id')):
                raise RuntimeBlocked('INVALID_ARCHIVED_EVENT')
            receipts.append(self.import_snapshot('LEDGER',
                            'studio/comms/'+event.relative_to(Path(comms_root)).as_posix(),
                            revision, record))
        return receipts

    def command(self, cmd: dict):
        # DB contract owns command validation, ACID transaction and authorization.
        return self.store.execute(cmd, self._context())

    def ack(self, *, job_id, worker_id, attempt_id, epoch, revision, event_id,
            effect_name, command_id):
        cmd = dict(schema_id='DB-CONTRACT/COMMAND/1',command_id=command_id,
                   command_kind='ACK',job_id=job_id,worker_id=worker_id,
                   attempt_id=attempt_id,assignment_epoch=epoch,
                   authority_revision=revision,
                   payload=dict(event_id=event_id, recipient_worker_id=worker_id,
                                delivery_assignment_epoch=epoch,effect_name=effect_name))
        return self.command(cmd)

    def checkpoint(self, cmd, resume_capsule=None):
        if cmd.get('command_kind') != 'CHECKPOINT':
            raise RuntimeBlocked('INVALID_COMMAND_KIND')
        result = self.command(cmd)
        if result['result'] == 'COMMITTED' and resume_capsule is not None:
            if (resume_capsule.get('attempt_id')!=cmd['attempt_id'] or
                resume_capsule.get('job_id')!=cmd['job_id'] or
                resume_capsule.get('assignment_epoch')!=cmd['assignment_epoch']):
                raise RuntimeBlocked('CAPSULE_FENCE_MISMATCH')
            self.store.save_resume_capsule(resume_capsule, self._context())
        return result

    def resume(self, job_id):
        return self.store.load_resume_capsule(_safe_id(job_id), self._context())

    def report_card(self, job_ids, max_jobs=200):
        """Deterministic, bounded status cards from DB fence/checkpoint observations.

        No assertion that a worker passed independent QA or published to GitHub.
        """
        if len(job_ids)>max_jobs or len(set(job_ids))!=len(job_ids):
            raise RuntimeBlocked('INVALID_JOB_SET')
        cards=[]
        for job_id in sorted(job_ids):
            row=self.store.resume_capsule(_safe_id(job_id),self._context())
            cards.append({'job_id':job_id,'attempt_id':row['attempt_id'],
                          'worker_id':row['worker_id'],'assignment_epoch':row['assignment_epoch'],
                          'lease_generation':row['lease_generation'],
                          'checkpoint_sha256':row['checkpoint_sha256'],
                          'authority':'SHADOW_ONLY','independent_qa':'NOT_VERIFIED'})
        return {'schema_id':'STUDIO-V16/HEAD-ROLLUP/1','jobs':cards,
                'workers':len({r['worker_id'] for r in cards}),
                'checkpoints':sum(bool(r['checkpoint_sha256']) for r in cards)}
