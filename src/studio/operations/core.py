"""WG-F operations domain. Requires WG-C to supply a scoped atomic named-plan port.

There is no production SQLite connection, model client, HTTP handler or inferred
project policy here. Mutations are committed by C's idempotent transaction
runner, audit events by C, and remote receipts/effects by the proper owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Callable, Mapping, Protocol, TypeVar

from .plans import OWNER

MAX_LIMIT = 200
RESOURCE_CLASSES = frozenset({"ci", "build", "storage", "hosting", "tool", "github"})
NOTIFICATION_KINDS = frozenset({"decision", "blocker", "release", "failure", "resource", "oversight"})
DELIVERY_STATES = frozenset({"queued", "sent", "failed", "unknown"})


class DomainError(Exception):
    """Map code directly to the frozen OpenAPI Error.code enumeration."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def fail(code: str, message: str) -> None:
    raise DomainError(code, message)


def required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        fail("invalid", f"{name} must be a nonempty bounded string")
    return value


def amount(value: int, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > 2**63-1:
        fail("invalid", f"{name} must be a nonnegative signed 64-bit integer")
    return value


def page_limit(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        fail("invalid", f"limit must be 1..{MAX_LIMIT}")
    return limit


def digest_payload(data: object) -> str:
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")).hexdigest()


def is_sha256(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        fail("invalid", "expected lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class Scope:
    project_id: str
    principal_id: str
    proof_ref: str
    allowed_project_ids: frozenset[str]

    def check(self) -> None:
        required(self.project_id, "project_id")
        required(self.principal_id, "principal_id")
        required(self.proof_ref, "trusted_proof_ref")
        if self.project_id not in self.allowed_project_ids:
            fail("forbidden", "principal has no project scope")


class NamedTransaction(Protocol):
    """WG-C adapter; names must be registered through plans.plans()."""
    def select_named(self, name: str, parameters: Mapping[str, Any]) -> list[Mapping[str, Any]]: ...
    def execute_named(self, name: str, parameters: Mapping[str, Any]) -> int: ...
    def record_event(self, *, event_type: str, subject_type: str, subject_id: str,
                     payload_digest: str) -> None: ...


T = TypeVar("T")
class CommandRunner(Protocol):
    """WG-C-owned atomic idempotency: same (owner,project,key,digest) replays exact result.

    Implementation MUST serialize business writes, verify Scope via J/A, commit
    domain mutation+event+idempotency together, and rollback on any exception.
    """
    def query(self, scope: Scope, fn: Callable[[NamedTransaction], T]) -> T: ...
    def command(self, scope: Scope, *, owner: str, request_id: str,
                body_digest: str, fn: Callable[[NamedTransaction], T]) -> T: ...


class UsageEvidenceVerifier(Protocol):
    """Trusted WG-G/host provider adapter: independently read back exact usage.

    A caller-supplied receipt digest alone cannot prove an actual charge.
    """
    def verify_usage(self, *, project_id: str, category: str, provider: str,
                     provider_ref: str, amount: int, evidence_digest: str) -> bool: ...


class ResourceClosureVerifier(Protocol):
    """Trusted coordinator/provider reconciliation says an operation cannot bill."""
    def verify_closure(self, *, project_id: str, operation_id: str,
                       reservation_id: str, reason: str) -> bool: ...


class VerifiedBlobWriter(Protocol):
    """WG-G: returns digest only after durable bytes/readback verification."""
    def store_verified(self, project_id: str, body: bytes, *, purpose: str) -> str: ...


class ObservedEffect(Protocol):
    """WG-G/I prove actual external remote readback before C records OBSERVED."""
    def observed(self, *, project_id: str, subject_type: str,
                 subject_id: str, action: str) -> bool: ...


class Operations:
    def __init__(self, runner: CommandRunner, *, usage_verifier: UsageEvidenceVerifier | None=None,
                 closure_verifier: ResourceClosureVerifier | None=None):
        self._runner = runner
        self._usage_verifier = usage_verifier
        self._closure_verifier = closure_verifier

    @staticmethod
    def _select_one(tx: NamedTransaction, plan: str, args: dict[str, Any]) -> Mapping[str, Any] | None:
        records = tx.select_named(plan, args)
        return records[0] if records else None

    def _read(self, scope: Scope, work: Callable[[NamedTransaction], T]) -> T:
        scope.check()
        return self._runner.query(scope, work)

    def _write(self, scope: Scope, op: str, request_id: str, body: object,
               work: Callable[[NamedTransaction], T]) -> T:
        scope.check()
        required(request_id, "request_id")
        return self._runner.command(scope, owner=OWNER,
                                    request_id=request_id,
                                    body_digest=digest_payload({"operation": op, "body": body}), fn=work)

    def configure_budget(self, scope: Scope, *, budget_id: str, limit_amount: int,
                         expected_revision: int, request_id: str) -> dict[str, Any]:
        """Wire configureBudget. Revision is C's committed budget.configured audit-event count.

        The frozen budgets table lacks a revision column. C must atomically append
        budget.configured exactly once per effective configuration commit. That
        is the revision oracle used here; a different C audit policy is a seam.
        """
        required(budget_id, "budget_id")
        amount(limit_amount, "limit_amount")
        amount(expected_revision, "expected_revision")
        def apply(tx: NamedTransaction) -> dict[str, Any]:
            rev = self._select_one(tx,"f.budget.revision",
                                   {"project_id":scope.project_id,"budget_id":budget_id})
            if int(rev["revision"]) != expected_revision:
                fail("stale_revision", "budget revision mismatch")
            p = {"project_id":scope.project_id,"budget_id":budget_id}
            before = self._select_one(tx,"f.budget.one",p)
            if before is None:
                if expected_revision != 0:
                    fail("stale_revision", "budget not yet configured")
                tx.execute_named("f.budget.insert", {**p,"resource_key":budget_id,"limit_amount":limit_amount})
            else:
                if int(before["spent_amount"])+int(before["reserved_amount"])>limit_amount:
                    fail("conflict", "limit below current spent and reserved sum")
                modified=tx.execute_named("f.budget.cas_limit", {**p,"limit_amount":limit_amount,
                    "old_limit":before["limit_amount"],"old_reserved":before["reserved_amount"],
                    "old_spent":before["spent_amount"]})
                if modified != 1:
                    fail("stale_revision", "concurrent budget update")
            tx.record_event(event_type="budget.configured",subject_type="budget",subject_id=budget_id,
                            payload_digest=digest_payload({"revision":expected_revision+1,"limit":limit_amount}))
            return {"budget_id":budget_id,"limit_amount":limit_amount}
        return self._write(scope,"configureBudget",request_id,
                           {"budget_id":budget_id,"limit_amount":limit_amount,
                            "expected_revision":expected_revision},apply)

    def get_budgets(self, scope: Scope, *, after: str = "", limit: int=MAX_LIMIT) -> dict[str, Any]:
        page_limit(limit)
        def read(tx: NamedTransaction) -> dict[str, Any]:
            records=tx.select_named("f.budget.list",{"project_id":scope.project_id,"after":after,"limit":limit})
            return {"budgets":[{k:r[k] for k in ("budget_id","limit_amount","reserved_amount","spent_amount")}
                               for r in records]}
        return self._read(scope,read)

    def reserve(self, scope: Scope, *, reservation_id: str, budget_id: str,
                operation_id: str, value: int, request_id: str) -> dict[str, Any]:
        """Must precede dispatch of any budgeted external/local operation."""
        for name,v in (("reservation_id",reservation_id),("budget_id",budget_id),("operation_id",operation_id)):
            required(v,name)
        amount(value,"value",positive=True)
        def apply(tx: NamedTransaction) -> dict[str, Any]:
            p={"project_id":scope.project_id,"reservation_id":reservation_id,
               "budget_id":budget_id,"operation_id":operation_id,"amount":value}
            existing=self._select_one(tx,"f.reservation.one",p)
            if existing:
                fail("conflict","reservation id already used")
            changed=tx.execute_named("f.budget.reserve",p)
            if changed!=1:
                fail("conflict","budget absent or insufficient unreserved capacity")
            tx.execute_named("f.reservation.insert",p)
            tx.record_event(event_type="resource.reserved",subject_type="reservation",subject_id=reservation_id,
                            payload_digest=digest_payload({"budget":budget_id,"amount":value,"operation":operation_id}))
            return {"reservation_id":reservation_id,"state":"reserved","amount":value}
        return self._write(scope,"reserve",request_id,{"reservation_id":reservation_id,
                "budget_id":budget_id,"operation_id":operation_id,"amount":value},apply)

    def reservations_for_operation(self, scope: Scope, *, operation_id: str,
                                   limit: int=MAX_LIMIT) -> list[dict[str, Any]]:
        """Project-scoped reconciliation query for D/I/G orchestrators."""
        required(operation_id,"operation_id")
        page_limit(limit)
        return self._read(scope,lambda tx:[dict(r) for r in tx.select_named(
            "f.reservation.by_operation",{"project_id":scope.project_id,
                                         "operation_id":operation_id,"limit":limit})])

    def consume(self, scope: Scope, *, reservation_id: str, usage_id: str,
                category: str, provider: str, provider_ref: str, actual_amount: int,
                evidence_digest: str, request_id: str) -> dict[str, Any]:
        scope.check()  # no external provider readback before tenant authorization
        for name,v in (("reservation_id",reservation_id),("usage_id",usage_id),
                       ("provider",provider),("provider_ref",provider_ref)):
            required(v,name)
        if category not in RESOURCE_CLASSES:
            fail("invalid","unsupported metered resource category")
        amount(actual_amount,"actual_amount")
        is_sha256(evidence_digest)
        # Consumption must be based on an independent observed provider/tool
        # receipt, never a caller-authored hash or a guessed external charge.
        # Verification is performed before starting C's local transaction.
        if self._usage_verifier is None:
            fail("capability_unavailable","trusted usage verification is not configured")
        if self._usage_verifier.verify_usage(project_id=scope.project_id,
                    category=category,provider=provider,provider_ref=provider_ref,
                    amount=actual_amount,evidence_digest=evidence_digest) is not True:
            fail("unknown_effect","usage receipt has no verified provider readback")
        # Unambiguous tagged provider receipts; no metered model-inference category.
        remote_key=json.dumps([category,provider,provider_ref],separators=(",",":"),ensure_ascii=False)
        def apply(tx: NamedTransaction) -> dict[str, Any]:
            p={"project_id":scope.project_id,"reservation_id":reservation_id}
            reservation=self._select_one(tx,"f.reservation.one",p)
            if not reservation:
                fail("missing","reservation not found in project")
            if reservation["state"]!="reserved":
                fail("conflict","reservation is no longer open")
            reserved=int(reservation["amount"])
            if actual_amount>reserved:
                fail("conflict","usage exceeds its reservation")
            if self._select_one(tx,"f.usage.by_provider",{"project_id":scope.project_id,"provider_ref":remote_key}):
                fail("conflict","provider usage reference already recorded")
            if self._select_one(tx,"f.usage.by_id",{"project_id":scope.project_id,"usage_id":usage_id}):
                fail("conflict","usage id already recorded")
            args={**p,"budget_id":reservation["budget_id"],"reserved":reserved,"actual":actual_amount}
            if tx.execute_named("f.budget.consume",args)!=1:
                fail("conflict","reserved budget accounting does not match")
            if tx.execute_named("f.reservation.consume",p)!=1:
                fail("conflict","reservation state changed")
            tx.execute_named("f.usage.insert",{**p,"usage_id":usage_id,"provider_ref":remote_key,
                "amount":actual_amount,"evidence_digest":evidence_digest})
            tx.record_event(event_type="resource.consumed",subject_type="usage",subject_id=usage_id,
                            payload_digest=digest_payload({"reservation":reservation_id,"usage":actual_amount,
                                                           "evidence":evidence_digest,"provider_ref":remote_key}))
            return {"usage_id":usage_id,"state":"consumed","charged_amount":actual_amount,
                    "released_amount":reserved-actual_amount}
        return self._write(scope,"consume",request_id,{"reservation_id":reservation_id,"usage_id":usage_id,
                 "category":category,"provider":provider,"provider_ref":provider_ref,
                 "actual_amount":actual_amount,"evidence_digest":evidence_digest},apply)

    def release(self, scope: Scope, *, reservation_id: str, request_id: str,
                reason: str) -> dict[str, Any]:
        required(reservation_id,"reservation_id")
        required(reason,"reason")
        scope.check()
        prior=self._read(scope,lambda tx:self._select_one(tx,"f.reservation.one",
                            {"project_id":scope.project_id,"reservation_id":reservation_id}))
        if not prior:
            fail("missing","reservation not found in project")
        if prior["state"]!="reserved":
            fail("conflict","only live reservations may be released")
        if self._closure_verifier is None:
            fail("capability_unavailable","cancellation/reconciliation proof is unavailable")
        # External reconciliation occurs *outside* WG-C's SQLite write lock.
        if self._closure_verifier.verify_closure(project_id=scope.project_id,
                    operation_id=prior["operation_id"],
                    reservation_id=reservation_id,reason=reason) is not True:
            fail("unknown_effect","operation has no proven cancellation/reconciliation")
        def apply(tx: NamedTransaction) -> dict[str, Any]:
            p={"project_id":scope.project_id,"reservation_id":reservation_id}
            reservation=self._select_one(tx,"f.reservation.one",p)
            if not reservation:
                fail("missing","reservation not found in project")
            if reservation["state"]!="reserved" or reservation["operation_id"]!=prior["operation_id"]:
                fail("conflict","reservation changed during closure verification")
            if tx.execute_named("f.budget.release",{**p,"budget_id":reservation["budget_id"],
                                                  "amount":reservation["amount"]})!=1:
                fail("conflict","budget does not match reservation")
            if tx.execute_named("f.reservation.release",p)!=1:
                fail("conflict","reservation state changed")
            tx.record_event(event_type="resource.released",subject_type="reservation",subject_id=reservation_id,
                            payload_digest=digest_payload({"reason":reason,"amount":reservation["amount"]}))
            return {"reservation_id":reservation_id,"state":"released"}
        return self._write(scope,"release",request_id,
                           {"reservation_id":reservation_id,"reason":reason},apply)

    def emit_notification(self, scope: Scope, *, notification_id: str, kind: str,
                          subject_id: str, request_id: str) -> dict[str, Any]:
        required(notification_id,"notification_id")
        required(subject_id,"subject_id")
        if kind not in NOTIFICATION_KINDS:
            fail("invalid","unknown notification kind")
        def apply(tx: NamedTransaction) -> dict[str, Any]:
            p={"project_id":scope.project_id,"notification_id":notification_id,
               "kind":kind,"subject_id":subject_id}
            if self._select_one(tx,"f.notification.one",p):
                fail("conflict","notification id in use")
            tx.execute_named("f.notification.insert",p)
            tx.record_event(event_type="notification.queued",subject_type="notification",subject_id=notification_id,
                            payload_digest=digest_payload({"kind":kind,"subject_id":subject_id}))
            return {"notification_id":notification_id,"delivery_state":"queued"}
        return self._write(scope,"emitNotification",request_id,
                           {"notification_id":notification_id,"kind":kind,"subject_id":subject_id},apply)

    def list_notifications(self, scope: Scope, *, after: str="", limit: int=MAX_LIMIT) -> dict[str, Any]:
        page_limit(limit)
        return self._read(scope,lambda tx:{"notifications":[
            {k:r[k] for k in ("notification_id","kind","delivery_state")}
            for r in tx.select_named("f.notification.list",{"project_id":scope.project_id,"after":after,"limit":limit})]})

    def ack_notification(self, scope: Scope, *, notification_id: str, expected_state: str,
                         request_id: str, observed_at: datetime | None=None) -> dict[str, Any]:
        required(notification_id,"notification_id")
        if expected_state not in DELIVERY_STATES:
            fail("invalid","invalid expected_state")
        # Production timestamp always server-derived; observed_at only supplies a deterministic test clock.
        now=observed_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            fail("invalid","timestamp must be timezone-aware")
        stamp=now.astimezone(timezone.utc).isoformat()
        def apply(tx: NamedTransaction) -> dict[str, Any]:
            p={"project_id":scope.project_id,"notification_id":notification_id,
               "expected_state":expected_state,"acknowledged_at":stamp,
               "ack_principal_id":scope.principal_id}
            if tx.execute_named("f.notification.ack",p)!=1:
                row=self._select_one(tx,"f.notification.one",p)
                if not row:
                    fail("missing","notification not found")
                fail("stale_revision","notification already acknowledged or delivery state changed")
            tx.record_event(event_type="notification.acknowledged",subject_type="notification",
                            subject_id=notification_id,payload_digest=digest_payload(
                                {"principal":scope.principal_id,"expected_state":expected_state}))
            return {"notification_id":notification_id,"acknowledged":True}
        return self._write(scope,"ackNotification",request_id,
                           {"notification_id":notification_id,"expected_state":expected_state},apply)

    def mark_delivery(self, scope: Scope, *, kind: str, record_id: str,
                      new_state: str, expected_state: str, request_id: str) -> dict[str,str]:
        """Only verified C/G/I effect observations may transition to sent.

        For reports, the frozen SQL trigger also enforces observed delivery effect.
        """
        required(record_id,"record_id")
        if kind not in ("report","notification") or new_state not in DELIVERY_STATES or expected_state not in DELIVERY_STATES:
            fail("invalid","invalid delivery transition")
        if new_state==expected_state or expected_state=="sent":
            fail("conflict","invalid delivery transition")
        def apply(tx: NamedTransaction) -> dict[str,str]:
            p={"project_id":scope.project_id}
            name="f.report.one" if kind=="report" else "f.notification.one"
            existing=self._select_one(tx,name,{**p,("report_id" if kind=="report" else "notification_id"):record_id})
            if not existing:
                fail("missing",f"{kind} not found")
            if existing["delivery_state"]!=expected_state:
                fail("stale_revision","delivery state changed")
            if new_state=="sent":
                evidence=self._select_one(tx,"f.effect.observed",{**p,"subject_type":kind,
                    "subject_id":record_id,"action":f"{kind}.deliver"})
                if not evidence:
                    fail("unknown_effect","external readback not observed")
            statement="f.report.delivery" if kind=="report" else "f.notification.delivery"
            params={**p,("report_id" if kind=="report" else "notification_id"):record_id,
                    "expected_state":expected_state,"delivery_state":new_state}
            if tx.execute_named(statement,params)!=1:
                fail("stale_revision","delivery CAS failed")
            tx.record_event(event_type=f"{kind}.delivery.{new_state}",subject_type=kind,
                            subject_id=record_id,payload_digest=digest_payload({"state":new_state}))
            return {"id":record_id,"delivery_state":new_state}
        return self._write(scope,"markDelivery",request_id,
                           {"kind":kind,"id":record_id,"new":new_state,"expected":expected_state},apply)

    def list_reports(self, scope: Scope, *, after: str="", limit: int=MAX_LIMIT) -> dict[str, Any]:
        page_limit(limit)
        return self._read(scope,lambda tx:{"reports":[dict(r) for r in
            tx.select_named("f.report.list",{"project_id":scope.project_id,"after":after,"limit":limit})]})
