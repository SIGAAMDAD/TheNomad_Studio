"""Deterministic, source-grounded project/department reporting for WG-F.

Report bytes are stored through the WG-G verified blob port before a WG-C
transaction records their digest. Relay transport and effect readback stay with
WG-I/G; a queued report is never presented as delivered.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Mapping, Protocol
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import (Operations, Scope, NamedTransaction, VerifiedBlobWriter,
                   digest_payload, is_sha256, required, fail, page_limit)


class ProfileStore(Protocol):
    """WG-G returns only verified immutable content matching digest/project."""
    def load_verified(self, project_id: str, digest: str) -> bytes: ...


class ReportRelay(Protocol):
    """WG-I prepares/outboxes GitHub relay; never assumes ChatGPT wakeup."""
    def queue_report(self, *, project_id: str, report_id: str,
                     report_digest: str, channel_id: str, request_id: str) -> None: ...


@dataclass(frozen=True)
class Schedule:
    """Owner-selected schedule sourced from verified profile; not a policy default."""
    timezone: str
    hour: int
    minute: int
    interval: str  # daily or weekly
    weekday: int | None = None

    @staticmethod
    def from_json(record: Mapping[str, Any]) -> "Schedule":
        tz=required(record.get("timezone"),"timezone")
        try:
            ZoneInfo(tz)
        except (KeyError,ZoneInfoNotFoundError):
            fail("invalid","invalid schedule timezone")
        interval=record.get("interval")
        if interval not in ("daily","weekly"):
            fail("invalid","interval must be daily or weekly")
        hour=record.get("hour")
        minute=record.get("minute")
        if type(hour) is not int or type(minute) is not int or not 0<=hour<=23 or not 0<=minute<=59:
            fail("invalid","invalid schedule time")
        weekday=record.get("weekday")
        if interval=="weekly" and (type(weekday) is not int or not 0<=weekday<=6):
            fail("invalid","weekly schedule needs weekday 0..6")
        if interval=="daily" and weekday is not None:
            fail("invalid","daily schedule must not specify weekday")
        return Schedule(tz,hour,minute,interval,weekday)

    def due_slot(self, now: datetime) -> datetime:
        """Latest due local wall-time slot; normalized to UTC, no auto dispatch.

        Nonexistent DST spring-forward local times resolve to next valid instant;
        ambiguous fall-back slots are only issued once when persisted last_slot
        is checked by the external scheduler through due().
        """
        if now.tzinfo is None:
            fail("invalid","schedule evaluation needs aware UTC clock")
        local=now.astimezone(ZoneInfo(self.timezone))
        date=local.date()
        for days in range(0,8):
            candidate=date-timedelta(days=days)
            if self.interval=="weekly" and candidate.weekday()!=self.weekday:
                continue
            # UTC roundtrip rejects imaginary wall times: advance by minutes.
            naive=datetime(candidate.year,candidate.month,candidate.day,self.hour,self.minute)
            for minute_offset in range(0,181):
                wall=naive+timedelta(minutes=minute_offset)
                aware=wall.replace(tzinfo=ZoneInfo(self.timezone),fold=0)
                utc=aware.astimezone(timezone.utc)
                if utc.astimezone(ZoneInfo(self.timezone)).replace(tzinfo=None)==wall:
                    if utc<=now.astimezone(timezone.utc):
                        return utc
                    break
        fail("invalid","no completed schedule slot")

    def due(self, now: datetime, last_completed_slot: datetime | None) -> tuple[bool,datetime]:
        slot=self.due_slot(now)
        if last_completed_slot is not None and last_completed_slot.tzinfo is None:
            fail("invalid","last_completed_slot must be aware")
        return (last_completed_slot is None or last_completed_slot.astimezone(timezone.utc)<slot),slot


@dataclass(frozen=True)
class ReportProfile:
    scope: str
    department_id: str | None
    max_budgets: int
    max_departments: int
    max_items_per_department: int
    max_events: int
    max_relay_receipts: int
    destination_channel_id: str | None
    schedule: Schedule | None

    @staticmethod
    def parse(body: bytes) -> "ReportProfile":
        try:
            data=json.loads(body)
        except (ValueError,UnicodeError,TypeError) as exc:
            fail("invalid",f"invalid report profile JSON: {exc}")
        if not isinstance(data,dict) or data.get("version")!=1 or data.get("scope") not in ("studio","department"):
            fail("invalid","unsupported report profile version/scope")
        def lim(key: str, default: int) -> int:
            number=data.get(key,default)
            page_limit(number)
            return number
        channel=data.get("destination_channel_id")
        if channel is not None:
            required(channel,"destination_channel_id")
        department_id=data.get("department_id")
        if data["scope"]=="department":
            required(department_id,"department_id")
        elif department_id is not None:
            fail("invalid","studio report cannot filter department_id")
        schedule=Schedule.from_json(data["schedule"]) if "schedule" in data else None
        return ReportProfile(data["scope"],department_id,lim("max_budgets",100),lim("max_departments",100),
                             lim("max_items_per_department",50),lim("max_events",50),
                             lim("max_relay_receipts",50),channel,schedule)


def _rows(tx:NamedTransaction,name:str,project_id:str,**kwargs:Any)->list[dict[str,Any]]:
    return [dict(r) for r in tx.select_named(name,{"project_id":project_id,**kwargs})]


def collect_report(tx:NamedTransaction, project_id:str, profile_digest:str,
                   profile:ReportProfile, clock:datetime)->dict[str,Any]:
    """One WG-C repeatable-read transaction; no unbounded result materialization.

    Every departmental task and relay receipt cites exact durable source keys;
    counts are observation summaries, not assertions of QA completion.
    """
    budgets=_rows(tx,"f.report.budget_aggregate",project_id,limit=profile.max_budgets)
    if profile.department_id:
        departments=_rows(tx,"f.report.department_one",project_id,department_id=profile.department_id)
        if not departments:
            fail("missing","department not staffed for this project")
    else:
        departments=_rows(tx,"f.report.department_summary",project_id,limit=profile.max_departments)
    for department in departments:
        department["evidence"]=_rows(tx,"f.report.department_evidence",project_id,
            department_id=department["department_id"],after="",limit=profile.max_items_per_department)
    events=_rows(tx,"f.report.recent_events",project_id,before=2**63-1,limit=profile.max_events)
    return {
        "format":"nomad-studio.report.v1", "project_id":project_id,
        "profile_digest":profile_digest,"scope":profile.scope,
        "generated_at":clock.astimezone(timezone.utc).isoformat(),
        "budgets":budgets,
        "usage":_rows(tx,"f.report.usage_totals",project_id)[0],
        "task_states":_rows(tx,"f.report.task_states",project_id),
        "oversight_states":_rows(tx,"f.report.oversight_states",project_id),
        "release_states":_rows(tx,"f.report.release_states",project_id),
        "notification_states":_rows(tx,"f.report.notification_states",project_id),
        "departments":departments,
        "recent_audit_events":events,
        "verified_relay_receipts":_rows(tx,"f.report.verified_relay",project_id,
                                        limit=profile.max_relay_receipts),
        "result_caps":{"budgets":profile.max_budgets,"departments":profile.max_departments,
            "department_evidence":profile.max_items_per_department,
            "audit_events":profile.max_events,"relay_receipts":profile.max_relay_receipts},
        "capped_lists_may_be_partial":True,
    }


def request_report(service: Operations, scope:Scope, *, request_id:str,
                   report_profile_digest:str, profiles: ProfileStore,
                   blobs: VerifiedBlobWriter, relay:ReportRelay|None=None,
                   observed_at:datetime|None=None)->dict[str,str]:
    required(request_id,"request_id")
    is_sha256(report_profile_digest)
    scope.check()
    report_id="rpt_"+digest_payload({"project_id":scope.project_id,"request_id":request_id})[:48]
    prior=service._read(scope,lambda tx:service._select_one(tx,"f.report.one",
                                      {"project_id":scope.project_id,"report_id":report_id}))
    if prior:
        # Still pass through C idempotency: a changed profile for the same key
        # is a conflict, even though the previous generated bytes exist.
        result=service._write(scope,"requestReport",request_id,
                              {"profile_digest":report_profile_digest},
                              lambda tx:{"report_id":report_id,"state":prior["delivery_state"]})
        # Re-drive an ambiguous/failed external queue attempt with the *stored*
        # content digest and the same WG-I idempotency key, not newly rendered bytes.
        if relay and result["state"] in ("queued","unknown"):
            raw=profiles.load_verified(scope.project_id,report_profile_digest)
            if sha256(raw).hexdigest()!=report_profile_digest:
                fail("conflict","profile bytes do not match supplied content digest")
            profile=ReportProfile.parse(raw)
            if profile.destination_channel_id:
                relay.queue_report(project_id=scope.project_id,report_id=report_id,
                    report_digest=prior["report_digest"],channel_id=profile.destination_channel_id,
                    request_id="report-delivery:"+request_id)
        return result
    raw=profiles.load_verified(scope.project_id,report_profile_digest)
    if sha256(raw).hexdigest()!=report_profile_digest:
        fail("conflict","profile bytes do not match supplied content digest")
    profile=ReportProfile.parse(raw)
    stamp=observed_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        fail("invalid","report clock must be timezone-aware")
    doc=service._read(scope,lambda tx:collect_report(tx,scope.project_id,
                                                      report_profile_digest,profile,stamp))
    content=json.dumps(doc,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    content_digest=sha256(content).hexdigest()
    durable_digest=blobs.store_verified(scope.project_id,content,purpose="report")
    if durable_digest!=content_digest:
        fail("conflict","verified report blob digest mismatch")
    def commit(tx:NamedTransaction)->dict[str,str]:
        p={"project_id":scope.project_id,"report_id":report_id,"report_digest":content_digest}
        row=service._select_one(tx,"f.report.one",p)
        if row:
            if row["report_digest"]!=content_digest:
                fail("conflict","same report ID has another digest")
            return {"report_id":report_id,"state":row["delivery_state"]}
        tx.execute_named("f.report.insert",p)
        tx.record_event(event_type="report.generated",subject_type="report",subject_id=report_id,
                        payload_digest=content_digest)
        return {"report_id":report_id,"state":"queued"}
    result=service._write(scope,"requestReport",request_id,
                          {"profile_digest":report_profile_digest},commit)
    # Outside the SQLite command. WG-I must independently journal/read back
    # any GitHub external write; failure leaves queued state for reconciliation.
    if relay and profile.destination_channel_id:
        relay.queue_report(project_id=scope.project_id,report_id=report_id,
                           report_digest=content_digest,channel_id=profile.destination_channel_id,
                           request_id="report-delivery:"+request_id)
    return result
