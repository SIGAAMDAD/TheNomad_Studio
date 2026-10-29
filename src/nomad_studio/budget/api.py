"""R9 frozen CapabilityPort budget operations, new-only tables.

Amounts are immutable *normalized billable budget units*. P2's trusted billing
adapter converts provider usage/cost to those units before settlement. Neither
task prose nor a caller-supplied price is authoritative billing evidence.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any

from nomad_studio.registry.results import Decision, Result

_ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
_HASH=re.compile(r'^[0-9a-f]{64}$')
_MAX=2**60


def _id(value: Any) -> bool:
    return isinstance(value,str) and bool(_ID.fullmatch(value))


def _hash(value: Any) -> bool:
    return isinstance(value,str) and bool(_HASH.fullmatch(value))


def _nonnegative(value: Any) -> bool:
    return type(value) is int and 0 <= value <= _MAX


def _row(conn: Any, sql: str, *args: Any) -> dict | None:
    row=conn.execute(sql,args).fetchone()
    return dict(row) if row is not None else None


class BudgetService:
    """Uses a P2 table-restricted serialized state writer, no direct DB ownership."""
    def __init__(self, deps: Mapping[str, Any]):
        self.reader=deps['reader']
        self.writer=deps['writer']
        self.feature_budget_reader=deps.get('feature_budget_reader')
        self.provider_billing_reader=deps.get('provider_billing_reader')
        self.clock_us=deps.get('clock_us')

    def reserve_budget(self, feature_id: str, requested_units: int, feature_budget_digest: str) -> Decision:
        if not _id(feature_id) or not _hash(feature_budget_digest) or not _nonnegative(requested_units) or requested_units == 0:
            return Decision(False,'DENY','INVALID_INPUT',feature_id if _id(feature_id) else 'invalid')
        if self.feature_budget_reader is None:
            return Decision(False,'UNKNOWN','PROVIDER_NOT_VERIFIED',feature_id)
        try:
            ctx=self.feature_budget_reader(feature_id)
            if (not isinstance(ctx,Mapping) or ctx.get('observed') is not True or
                ctx.get('feature_id') != feature_id or ctx.get('budget_digest') != feature_budget_digest or
                not _id(ctx.get('cabal_id')) or not _id(ctx.get('provider_id')) or
                not _id(ctx.get('project_id')) or not _id(ctx.get('evidence_ref')) or
                not _nonnegative(ctx.get('budget_limit_units')) or
                not isinstance(ctx.get('reservation_epoch'),int) or ctx['reservation_epoch'] < 1):
                return Decision(False,'DENY','EVIDENCE_INVALID',feature_id)
        except Exception:
            return Decision(False,'UNKNOWN','PROVIDER_UNKNOWN',feature_id)
        reservation_id='res:'+sha256(json.dumps({'feature_id':feature_id,'budget_digest':feature_budget_digest,
            'cabal_id':ctx['cabal_id'],'provider_id':ctx['provider_id'],
            'requested_units':requested_units,'reservation_epoch':ctx['reservation_epoch']},
            sort_keys=True,separators=(',',':')).encode()).hexdigest()
        def reserve(conn: Any) -> tuple[str,str]:
            run=_row(conn,'SELECT f.project_id,c.budget_limit_units,c.bible_pair_digest FROM v1_feature_runs f '
                'JOIN v1_feature_contracts c ON c.project_id=f.project_id AND c.feature_id=f.feature_id '
                'AND c.contract_digest=f.active_contract_digest WHERE f.feature_id=?',feature_id)
            if not run or run['project_id'] != ctx['project_id']:
                return ('DENY','WRONG_PROJECT')
            if ctx['budget_limit_units'] > run['budget_limit_units'] or run['bible_pair_digest'] is None:
                return ('DENY','CAPACITY_LIMIT')
            existing=_row(conn,'SELECT * FROM v1_budget_reservations WHERE reservation_id=?',reservation_id)
            if existing is not None:
                if (existing['feature_id']==feature_id and existing['provider_id']==ctx['provider_id'] and
                    existing['cabal_id']==ctx['cabal_id'] and existing['unit_limit']==requested_units):
                    return ('ALLOW','OK')
                return ('DENY','IDEMPOTENCY_CONFLICT')
            used=conn.execute('SELECT COALESCE(SUM(reserved_units+measured_units),0) FROM v1_budget_reservations '
                'WHERE feature_id=?',(feature_id,)).fetchone()[0]
            if not _nonnegative(used) or used > ctx['budget_limit_units'] or requested_units > ctx['budget_limit_units']-used:
                return ('DENY','CAPACITY_LIMIT')
            conn.execute('INSERT INTO v1_budget_reservations(feature_id,reservation_id,cabal_id,provider_id,unit_limit,reserved_units,measured_units,state) '
                'VALUES(?,?,?,?,?,?,0,?)',(feature_id,reservation_id,ctx['cabal_id'],ctx['provider_id'],
                                              requested_units,requested_units,'RESERVED'))
            return ('ALLOW','OK')
        try:
            status,code=self.writer.write(reserve)
        except Exception:
            return Decision(False,'HOLD_LOCAL','LOCAL_RECOVERY_REQUIRED',feature_id)
        return Decision(status=='ALLOW',status,code,reservation_id if status=='ALLOW' else feature_id,
                        (ctx['evidence_ref'],))

    def reconcile_spend(self, reservation_id: str, provider_charge_receipt: Mapping[str, Any]) -> Result:
        if not _id(reservation_id) or not isinstance(provider_charge_receipt,Mapping):
            return Result('REJECTED','INVALID_INPUT')
        if self.provider_billing_reader is None or self.clock_us is None:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        try:
            # Provider readback is performed BEFORE opening SQLite write lock.
            observed=self.provider_billing_reader(reservation_id,provider_charge_receipt)
            timestamp=self.clock_us()
            if (not isinstance(observed,Mapping) or observed.get('observed') is not True or
                observed.get('reservation_id') != reservation_id or
                not _id(observed.get('provider_id')) or
                not _id(observed.get('provider_receipt_ref')) or
                not _nonnegative(observed.get('measured_units')) or
                observed.get('is_final') not in (True,False) or
                not _id(observed.get('evidence_ref')) or not _nonnegative(timestamp)):
                return Result('NOT_VERIFIED','EVIDENCE_INVALID')
        except Exception:
            return Result('NOT_VERIFIED','PROVIDER_NOT_VERIFIED')
        receipt_ref=observed['provider_receipt_ref']
        units=observed['measured_units']
        is_final=observed['is_final']
        settlement_id='settle:'+sha256((reservation_id+'|'+receipt_ref).encode()).hexdigest()
        def settle(conn: Any) -> tuple[str,str,dict | None]:
            res=_row(conn,'SELECT * FROM v1_budget_reservations WHERE reservation_id=?',reservation_id)
            if res is None:
                return ('NOT_FOUND','NOT_FOUND',None)
            if res['provider_id'] != observed['provider_id']:
                return ('REJECTED','EVIDENCE_INVALID',None)
            previous=_row(conn,'SELECT * FROM v1_budget_settlements WHERE provider_receipt_ref=?',receipt_ref)
            if previous is not None:
                if (previous['reservation_id']==reservation_id and previous['measured_units']==units and
                    previous['settlement_id']==settlement_id):
                    return ('OK','OK',{'reservation_id':reservation_id,'settlement_id':settlement_id,'replay':True})
                return ('CONFLICT','IDEMPOTENCY_CONFLICT',None)
            if res['state'] != 'RESERVED' or units > res['reserved_units']:
                return ('REJECTED','CAPACITY_LIMIT',None)
            if units==0 and not is_final:
                return ('REJECTED','INVALID_INPUT',None)
            reserved_new=0 if is_final else res['reserved_units']-units
            measured_new=res['measured_units']+units
            if not _nonnegative(measured_new):
                return ('REJECTED','CAPACITY_LIMIT',None)
            conn.execute('INSERT INTO v1_budget_settlements(settlement_id,reservation_id,provider_receipt_ref,measured_units,recorded_at_us) '
                'VALUES(?,?,?,?,?)',(settlement_id,reservation_id,receipt_ref,units,timestamp))
            conn.execute('UPDATE v1_budget_reservations SET reserved_units=?,measured_units=?,state=? WHERE reservation_id=?',
                (reserved_new,measured_new,'SETTLED' if is_final else 'RESERVED',reservation_id))
            return ('OK','OK',{'reservation_id':reservation_id,'settlement_id':settlement_id,
                'measured_units_total':measured_new,'reserved_units':reserved_new,'final':is_final,'replay':False})
        try:
            status,code,record=self.writer.write(settle)
        except Exception:
            return Result('BLOCKED_LOCAL','LOCAL_RECOVERY_REQUIRED')
        return Result(status,code,record,settlement_id if status=='OK' else None,
                      (observed['evidence_ref'],))


def build_budget_port(deps: Mapping[str, Any]) -> BudgetService:
    return BudgetService(deps)
