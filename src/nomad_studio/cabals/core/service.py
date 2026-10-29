"""R9 CAB-A: durable, scoped, qualified cabals, not departmental authorities.

The composition root MUST inject a read capability and a transaction capability
restricted to CAB-A's three writable v1_ tables. Never inject the unrestricted
legacy StateStore. Bible snapshots are read outside our writer transaction; the
active edition/epoch is then rechecked INSIDE the transaction to close the race.

Only the four public CabalPort operations are exposed. This service cannot mint
identity, activate a Bible, approve an effect, or edit a feature contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Callable, Mapping, Protocol, Sequence, TypeVar

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_MAX_MEMBERS = 64
_MAX_SKILLS = 64
_PRODUCTION_PHASES = frozenset({"PLANNED", "IN_PRODUCTION", "REWORK_REQUIRED"})
T = TypeVar("T")


from nomad_studio.contracts.runtime_ports_v1 import Result, Decision


class SQLView(Protocol):
    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Mapping[str, Any] | None: ...
    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> Sequence[Mapping[str, Any]]: ...


class OwnedSQL(SQLView, Protocol):
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int: ...


class CabalStorage(Protocol):
    """P2 provides a single-writer, CAB-A-table-limited transaction boundary."""
    def read(self, query: Callable[[SQLView], T]) -> T: ...
    def write_owned(self, command: Callable[[OwnedSQL], T]) -> T: ...


class BibleSnapshotReader(Protocol):
    def snapshot_active_pair(self, project_id: str, feature_scope: Mapping[str, Any]) -> Any: ...


class CabalError(Exception):
    def __init__(self, code: str, status: str = "REJECTED") -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CabalError("INVALID_INPUT")
    return value


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise CabalError("INVALID_INPUT")
    return value


def _integer(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise CabalError("INVALID_INPUT")
    return value


def _identifiers(value: Any, *, max_items: int = _MAX_SKILLS, empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > max_items or (not empty and not value):
        raise CabalError("INVALID_INPUT")
    result = tuple(_id(v) for v in value)
    if len(set(result)) != len(result):
        raise CabalError("INVALID_INPUT")
    return result


def _json_hash(record: Mapping[str, Any]) -> str:
    return sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _feature_scope(row: Mapping[str, Any]) -> str:
    """Feature-specific charter scope in existing DDL's opaque digest column."""
    return _json_hash({"project_id": row["project_id"], "feature_id": row["feature_id"],
                       "contract_digest": row["active_contract_digest"],
                       "bible_pair_digest": row["bible_pair_digest"],
                       "feature_scope_digest": row["scope_digest"]})


def _task_schema(raw: Any) -> dict[str, Any]:
    """Validate the exact frozen TaskSpec record, including all closed fields."""
    if not isinstance(raw, Mapping):
        raise CabalError("INVALID_INPUT")
    allowed = {"project_id", "feature_id", "task_id", "graph_revision", "cabal_id",
               "bible_pair_digest", "feature_contract_digest", "dependencies", "required_skills",
               "owned_resources", "input_digests", "outputs", "acceptance_ids",
               "budget_limit_units", "task_digest"}
    if set(raw) != allowed or any(type(k) is not str for k in raw):
        raise CabalError("INVALID_INPUT")
    d = dict(raw)
    for key in ("project_id", "feature_id", "task_id", "cabal_id"):
        _id(d[key])
    for key in ("bible_pair_digest", "feature_contract_digest", "task_digest"):
        _hash(d[key])
    for key in ("graph_revision",):
        _integer(d[key], 1)
    _integer(d["budget_limit_units"])
    for key in ("dependencies", "required_skills", "owned_resources", "outputs", "acceptance_ids"):
        if type(d[key]) is not list:
            raise CabalError("INVALID_INPUT")
        _identifiers(d[key], max_items=4096, empty=True)
    digests = d["input_digests"]
    if (not isinstance(digests, list) or not all(isinstance(d, str) for d in digests)
            or len(digests) != len(set(digests))):
        raise CabalError("INVALID_INPUT")
    for digest in digests:
        _hash(digest)
    return d


class DeliberationPolicyReader(Protocol):
    def assess_bible_conflict(self, feature_id: str, decision_or_diff: Mapping[str, Any],
                              bible_pair_digest: str) -> Any: ...


class CabalService:
    """Implementation of all four frozen CabalPort signatures.

    A verified identity reader is an injected CAB-H-owned read surface. It is
    *not* inferred from a caller-controlled worker field. Qualification rows
    are CAB-G-owned; this service merely checks their trusted stored evidence.
    """

    def __init__(self, storage: CabalStorage, bible: BibleSnapshotReader,
                 authority: DeliberationPolicyReader, *, clock_us: Callable[[], int],
                 authenticated_worker: Callable[[], str | None]):
        self._storage = storage
        self._bible = bible
        self._authority = authority
        self._clock = clock_us
        self._worker = authenticated_worker

    @staticmethod
    def _result_error(error: CabalError) -> Result:
        return Result(error.status, error.code)

    @staticmethod
    def _decision_error(error: CabalError, target: str) -> Decision:
        return Decision(False, "DENY", error.code, target)

    def _now(self) -> int:
        return _integer(self._clock(), 1)

    def _snapshot(self, project_id: str, feature_id: str, digest: str) -> Mapping[str, Any]:
        observation = self._bible.snapshot_active_pair(project_id, {"feature_id": feature_id})
        if getattr(observation, "status", None) not in ("OK", "ACCEPTED"):
            raise CabalError("STALE_BIBLE", "CONFLICT")
        record = getattr(observation, "record", None)
        if not isinstance(record, Mapping) or record.get("project_id") != project_id or record.get("pair_digest") != digest:
            raise CabalError("STALE_BIBLE", "CONFLICT")
        required = {"project_id", "design_edition_id", "technical_edition_id",
                    "design_epoch", "technical_epoch", "design_manifest_digest",
                    "technical_manifest_digest", "pair_digest"}
        if set(record) != required:
            raise CabalError("EVIDENCE_INVALID")
        for name in ("design_edition_id", "technical_edition_id"):
            _id(record.get(name))
        for name in ("design_epoch", "technical_epoch"):
            _integer(record.get(name))
        for name in ("design_manifest_digest", "technical_manifest_digest", "pair_digest"):
            _hash(record.get(name))
        return record

    @staticmethod
    def _current_pair(tx: SQLView, project_id: str, snapshot: Mapping[str, Any]) -> None:
        rows = tx.fetch_all("SELECT kind,edition_id,epoch,manifest_digest FROM v1_bible_editions "
                            "WHERE project_id=? AND active=1", (project_id,))
        by_kind = {r["kind"]: r for r in rows}
        if len(rows) != 2 or set(by_kind) != {"DESIGN", "TECHNICAL"}:
            raise CabalError("STALE_BIBLE", "CONFLICT")
        for kind, prefix in (("DESIGN", "design"), ("TECHNICAL", "technical")):
            row = by_kind[kind]
            if (row["edition_id"] != snapshot[prefix + "_edition_id"] or
                row["epoch"] != snapshot[prefix + "_epoch"] or
                row["manifest_digest"] != snapshot[prefix + "_manifest_digest"]):
                raise CabalError("STALE_BIBLE", "CONFLICT")

    @staticmethod
    def _feature(tx: SQLView, feature_id: str) -> Mapping[str, Any]:
        row = tx.fetch_one("SELECT f.project_id, f.feature_id, f.bible_pair_digest, "
                           "f.active_contract_digest, f.graph_revision, f.phase, "
                           "p.state AS project_state,p.authority_epoch,c.scope_digest "
                           "FROM v1_feature_runs AS f "
                           "JOIN v1_projects AS p ON p.project_id=f.project_id "
                           "JOIN v1_feature_contracts AS c "
                           "ON c.project_id=f.project_id AND c.feature_id=f.feature_id "
                           "AND c.contract_digest=f.active_contract_digest "
                           "WHERE f.feature_id=?", (feature_id,))
        if row is None:
            raise CabalError("NOT_FOUND", "NOT_FOUND")
        if row["project_state"] != "ACTIVE":
            raise CabalError("PERMISSION_DENIED")
        return row

    @staticmethod
    def _must_be_producing(feature: Mapping[str, Any]) -> None:
        if feature["phase"] not in _PRODUCTION_PHASES:
            raise CabalError("PERMISSION_DENIED")

    @staticmethod
    def _cabal(tx: SQLView, project_id: str, cabal_id: str, feature: Mapping[str, Any]) -> Mapping[str, Any]:
        row = tx.fetch_one("SELECT * FROM v1_cabals WHERE project_id=? AND cabal_id=?",
                           (project_id, cabal_id))
        if row is None:
            raise CabalError("NOT_FOUND", "NOT_FOUND")
        if row["state"] != "ACTIVE":
            raise CabalError("PERMISSION_DENIED")
        if row["charter_epoch"] != feature["authority_epoch"]:
            raise CabalError("REVISION_CONFLICT", "CONFLICT")
        if row["scope_digest"] != _feature_scope(feature):
            raise CabalError("SCOPE_EXCEEDED")
        if row["bible_pair_digest"] != feature["bible_pair_digest"]:
            raise CabalError("STALE_BIBLE", "CONFLICT")
        return row

    @staticmethod
    def _eligible(tx: SQLView, project_id: str, now: int, skills: Sequence[str]) -> dict[str, dict[str, Any]]:
        # All evidence is persisted by CAB-G and CAB-H. No model-supplied
        # qualification or owner/admin role is accepted as authority.
        if not skills:
            return {}
        marks = ",".join("?" for _ in skills)
        rows = tx.fetch_all(
            "SELECT wc.worker_id,wc.skill_id,wc.verified_evidence_ref, "
            "i.valid_until_us,s.project_scope_id "
            "FROM v1_worker_capabilities wc "
            "JOIN v1_skill_capabilities s ON s.skill_id=wc.skill_id "
            "JOIN v1_verified_identities i ON i.principal_id=wc.worker_id "
            "WHERE wc.available=1 AND wc.verified_evidence_ref<>'' AND i.evidence_ref<>'' "
            f"AND i.valid_until_us>? AND wc.skill_id IN ({marks}) "
            "AND (s.project_scope_id IS NULL OR s.project_scope_id=?)",
            (now, *skills, project_id))
        eligible: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = eligible.setdefault(row["worker_id"], {"skills": set(), "until": row["valid_until_us"]})
            item["skills"].add(row["skill_id"])
            item["until"] = max(item["until"], row["valid_until_us"])
        return eligible

    @staticmethod
    def _roster(tx: SQLView, project_id: str, cabal_id: str, now: int) -> list[dict[str, Any]]:
        rows = tx.fetch_all("SELECT m.worker_id,m.role_ref,m.valid_until_us FROM v1_memberships m "
                            "WHERE m.project_id=? AND m.cabal_id=? ORDER BY m.worker_id",
                            (project_id, cabal_id))
        return [dict(row) for row in rows if row["valid_until_us"] > now]

    @staticmethod
    def _has_qualification(tx: SQLView, project_id: str, member: Mapping[str, Any], now: int) -> bool:
        return tx.fetch_one("SELECT 1 AS yes FROM v1_worker_capabilities wc "
                            "JOIN v1_skill_capabilities s ON s.skill_id=wc.skill_id "
                            "JOIN v1_verified_identities i ON i.principal_id=wc.worker_id "
                            "WHERE wc.worker_id=? AND wc.skill_id=? AND wc.available=1 "
                            "AND wc.verified_evidence_ref<>'' AND i.evidence_ref<>'' "
                            "AND i.valid_until_us>? AND (s.project_scope_id IS NULL OR s.project_scope_id=?) "
                            "LIMIT 1", (member["worker_id"], member["role_ref"], now, project_id)) is not None

    def reserve_cabals(self, feature_id: str, bible_pair_digest: str, required_capabilities: Sequence[str]) -> Result:
        try:
            feature_id, bible_pair_digest = _id(feature_id), _hash(bible_pair_digest)
            skills = _identifiers(required_capabilities)
            now = self._now()
            # Read-only resolution precedes single-authority transaction.
            feature = self._storage.read(lambda tx: self._feature(tx, feature_id))
            snapshot = self._snapshot(feature["project_id"], feature_id, bible_pair_digest)
            if feature["bible_pair_digest"] != bible_pair_digest:
                raise CabalError("STALE_BIBLE", "CONFLICT")

            def reserve(tx: OwnedSQL) -> Mapping[str, Any]:
                current = self._feature(tx, feature_id)
                if current["bible_pair_digest"] != bible_pair_digest:
                    raise CabalError("STALE_BIBLE", "CONFLICT")
                self._must_be_producing(current)
                self._current_pair(tx, current["project_id"], snapshot)
                members = self._eligible(tx, current["project_id"], now, skills)
                uncovered = set(skills)
                chosen: list[str] = []
                while uncovered:
                    ranked = sorted(members, key=lambda wid: (-len(members[wid]["skills"] & uncovered), wid))
                    if not ranked or not (members[ranked[0]]["skills"] & uncovered):
                        raise CabalError("CAPACITY_LIMIT", "BLOCKED_LOCAL")
                    worker = ranked[0]
                    chosen.append(worker)
                    uncovered.difference_update(members[worker]["skills"])
                    if len(chosen) > _MAX_MEMBERS:
                        raise CabalError("CAPACITY_LIMIT", "BLOCKED_LOCAL")
                scope = _feature_scope(current)
                cabal_id = "cab-" + _json_hash({"project": current["project_id"], "feature": feature_id,
                                                   "contract": current["active_contract_digest"],
                                                   "pair": bible_pair_digest, "skills": sorted(skills)})[:32]
                existing = tx.fetch_one("SELECT * FROM v1_cabals WHERE project_id=? AND cabal_id=?",
                                        (current["project_id"], cabal_id))
                if existing is None:
                    tx.execute("INSERT INTO v1_cabals(project_id,cabal_id,charter_epoch,scope_digest,bible_pair_digest,state) "
                               "VALUES (?,?,?,?,?,'ACTIVE')",
                               (current["project_id"], cabal_id, current["authority_epoch"], scope, bible_pair_digest))
                elif (existing["scope_digest"] != scope or existing["bible_pair_digest"] != bible_pair_digest or
                      existing["charter_epoch"] != current["authority_epoch"] or existing["state"] != "ACTIVE"):
                    raise CabalError("REVISION_CONFLICT", "CONFLICT")
                else:
                    old = {m["worker_id"] for m in self._roster(tx, current["project_id"], cabal_id, now)}
                    if not set(chosen).issubset(old):
                        # An existing charter cannot silently gain members after first admission.
                        raise CabalError("REVISION_CONFLICT", "CONFLICT")
                for worker in chosen:
                    role = sorted(members[worker]["skills"] & set(skills))[0]
                    tx.execute("INSERT OR IGNORE INTO v1_memberships(project_id,cabal_id,worker_id,role_ref,valid_until_us) "
                               "VALUES (?,?,?,?,?)", (current["project_id"], cabal_id, worker, role, members[worker]["until"]))
                return {"project_id": current["project_id"], "feature_id": feature_id, "cabal_id": cabal_id,
                        "bible_pair_digest": bible_pair_digest, "scope_digest": scope,
                        "charter_epoch": current["authority_epoch"], "member_ids": tuple(sorted(chosen))}

            return Result("OK", "OK", self._storage.write_owned(reserve))
        except CabalError as exc:
            return self._result_error(exc)

    def authorize_cabal_task(self, cabal_id: str, worker_principal: str, task: Mapping[str, Any]) -> Decision:
        target = cabal_id if isinstance(cabal_id, str) else "INVALID"
        try:
            cabal_id, worker_principal, task = _id(cabal_id), _id(worker_principal), _task_schema(task)
            now = self._now()
            if self._worker() != worker_principal:
                raise CabalError("UNAUTHENTICATED")
            if task["cabal_id"] != cabal_id:
                raise CabalError("SCOPE_EXCEEDED")
            feature = self._storage.read(lambda tx: self._feature(tx, task["feature_id"]))
            if feature["project_id"] != task["project_id"]:
                raise CabalError("WRONG_PROJECT")
            snapshot = self._snapshot(feature["project_id"], task["feature_id"], task["bible_pair_digest"])

            def check(tx: SQLView) -> None:
                current = self._feature(tx, task["feature_id"])
                if current["project_id"] != task["project_id"]:
                    raise CabalError("WRONG_PROJECT")
                self._must_be_producing(current)
                if current["bible_pair_digest"] != task["bible_pair_digest"]:
                    raise CabalError("STALE_BIBLE", "CONFLICT")
                if current["active_contract_digest"] != task["feature_contract_digest"]:
                    raise CabalError("REVISION_CONFLICT", "CONFLICT")
                if current["graph_revision"] != task["graph_revision"]:
                    raise CabalError("REVISION_CONFLICT", "CONFLICT")
                self._current_pair(tx, current["project_id"], snapshot)
                self._cabal(tx, current["project_id"], cabal_id, current)
                persisted = tx.fetch_one("SELECT feature_id,graph_revision,task_digest,payload_json FROM v1_tasks "
                                         "WHERE task_id=? AND cabal_id=?",
                                         (task["task_id"], cabal_id))
                if persisted is None or persisted["feature_id"] != task["feature_id"]:
                    raise CabalError("WRONG_FEATURE")
                if persisted["graph_revision"] != task["graph_revision"] or persisted["task_digest"] != task["task_digest"]:
                    raise CabalError("REVISION_CONFLICT", "CONFLICT")
                try:
                    stored_task = json.loads(persisted["payload_json"])
                except (TypeError, ValueError):
                    raise CabalError("EVIDENCE_INVALID") from None
                if stored_task != task:
                    raise CabalError("EVIDENCE_INVALID")
                roster = self._roster(tx, current["project_id"], cabal_id, now)
                matching = next((r for r in roster if r["worker_id"] == worker_principal), None)
                if matching is None or not self._has_qualification(tx, current["project_id"], matching, now):
                    raise CabalError("PERMISSION_DENIED")
                if not any(self._has_qualification(tx, current["project_id"],
                                                   {**matching, "role_ref": skill}, now)
                           for skill in task["required_skills"]):
                    raise CabalError("PERMISSION_DENIED")
            self._storage.read(check)
            return Decision(True, "ALLOW", "OK", task["task_id"])
        except CabalError as exc:
            return self._decision_error(exc, target)

    def open_deliberation(self, cabal_id: str, feature_id: str, scoped_question: Mapping[str, Any]) -> Result:
        """Creates durable temporary subcabal with a subset of the parent roster.

        scoped_question requires question_text, its SHA-256 and member_ids.
        CAB-D checks the actual question against the active bibles. The
        initiator is trusted ingress context, never an agent-supplied identity.
        All subgroup members remain under the SAME feature, scope and Bible pair.
        """
        try:
            cabal_id, feature_id = _id(cabal_id), _id(feature_id)
            if not isinstance(scoped_question, Mapping) or set(scoped_question) != {"question_text", "question_digest", "member_ids"}:
                raise CabalError("INVALID_INPUT")
            question_digest = _hash(scoped_question["question_digest"])
            question_text = scoped_question["question_text"]
            if (not isinstance(question_text, str) or not question_text.strip() or
                len(question_text) > 16384 or "\x00" in question_text or
                sha256(question_text.encode("utf-8")).hexdigest() != question_digest):
                raise CabalError("INVALID_INPUT")
            requested = _identifiers(scoped_question["member_ids"], max_items=_MAX_MEMBERS)
            initiator = _id(self._worker())
            now = self._now()
            feature = self._storage.read(lambda tx: self._feature(tx, feature_id))
            snapshot = self._snapshot(feature["project_id"], feature_id, feature["bible_pair_digest"])
            compliance = self._authority.assess_bible_conflict(feature_id,
                {"kind": "DELIBERATION_QUESTION", "question_text": question_text,
                 "question_digest": question_digest, "project_id": feature["project_id"],
                 "scope_digest": _feature_scope(feature)}, feature["bible_pair_digest"])
            if getattr(compliance, "status", None) not in ("OK", "ACCEPTED") or getattr(compliance, "code", None) != "OK":
                raise CabalError("BIBLE_CONFLICT" if getattr(compliance, "code", None) == "BIBLE_CONFLICT"
                                 else "EVIDENCE_INVALID")

            def create(tx: OwnedSQL) -> Mapping[str, Any]:
                current = self._feature(tx, feature_id)
                self._must_be_producing(current)
                if (current["project_id"] != feature["project_id"] or
                    current["bible_pair_digest"] != feature["bible_pair_digest"] or
                    _feature_scope(current) != _feature_scope(feature)):
                    raise CabalError("REVISION_CONFLICT", "CONFLICT")
                self._current_pair(tx, current["project_id"], snapshot)
                parent = self._cabal(tx, current["project_id"], cabal_id, current)
                roster = {r["worker_id"]: r for r in self._roster(tx, current["project_id"], cabal_id, now)
                          if self._has_qualification(tx, current["project_id"], r, now)}
                if initiator not in roster or not set(requested).issubset(roster):
                    raise CabalError("PERMISSION_DENIED")
                # Both id and scope bind to current feature contract and immutable
                # subset. No Bible/owner/charter authority is delegated to a group.
                group_id = "grp-" + _json_hash({"parent": cabal_id, "feature": feature_id,
                                                   "question": question_digest,
                                                   "members": sorted(requested),
                                                   "scope": parent["scope_digest"]})[:32]
                exists = tx.fetch_one("SELECT group_id,status FROM v1_deliberations WHERE group_id=?", (group_id,))
                if exists is not None:
                    if exists["status"] != "OPEN":
                        raise CabalError("REVISION_CONFLICT", "CONFLICT")
                    current_members = {r["worker_id"] for r in self._roster(tx, current["project_id"], group_id, now)}
                    if current_members != set(requested):
                        raise CabalError("REVISION_CONFLICT", "CONFLICT")
                    return {"project_id": current["project_id"], "feature_id": feature_id,
                            "parent_cabal_id": cabal_id, "cabal_id": group_id, "group_id": group_id,
                            "bible_pair_digest": current["bible_pair_digest"],
                            "scope_digest": parent["scope_digest"], "member_ids": tuple(sorted(requested))}
                tx.execute("INSERT INTO v1_cabals(project_id,cabal_id,charter_epoch,scope_digest,bible_pair_digest,state) "
                           "VALUES (?,?,?,?,?,'ACTIVE')", (current["project_id"], group_id,
                           parent["charter_epoch"], parent["scope_digest"], parent["bible_pair_digest"]))
                for worker in sorted(requested):
                    member = roster[worker]
                    tx.execute("INSERT INTO v1_memberships(project_id,cabal_id,worker_id,role_ref,valid_until_us) "
                               "VALUES (?,?,?,?,?)", (current["project_id"], group_id, worker,
                               member["role_ref"], member["valid_until_us"]))
                tx.execute("INSERT INTO v1_deliberations(project_id,group_id,cabal_id,feature_id,scope_digest,status,opened_us) "
                           "VALUES (?,?,?,?,?,'OPEN',?)", (current["project_id"], group_id, group_id,
                           feature_id, parent["scope_digest"], now))
                return {"project_id": current["project_id"], "feature_id": feature_id,
                        "parent_cabal_id": cabal_id, "cabal_id": group_id, "group_id": group_id,
                        "bible_pair_digest": current["bible_pair_digest"],
                        "scope_digest": parent["scope_digest"], "member_ids": tuple(sorted(requested))}

            return Result("OK", "OK", self._storage.write_owned(create))
        except CabalError as exc:
            return self._result_error(exc)

    def get_cabal_roster(self, project_id: str, cabal_id: str) -> Result:
        try:
            project_id, cabal_id = _id(project_id), _id(cabal_id)
            now = self._now()
            # Obtain project and cabal before calling BiblePort (no nested read).
            def locate(tx: SQLView) -> Mapping[str, Any]:
                cabal = tx.fetch_one("SELECT * FROM v1_cabals WHERE project_id=? AND cabal_id=?",
                                     (project_id, cabal_id))
                if cabal is None:
                    raise CabalError("NOT_FOUND", "NOT_FOUND")
                features = tx.fetch_all("SELECT f.project_id,f.feature_id,f.bible_pair_digest, "
                                        "f.active_contract_digest,f.graph_revision,f.phase, "
                                        "p.state AS project_state,p.authority_epoch,c.scope_digest "
                                        "FROM v1_feature_runs f JOIN v1_projects p ON f.project_id=p.project_id "
                                        "JOIN v1_feature_contracts c ON c.project_id=f.project_id AND "
                                        "c.feature_id=f.feature_id AND c.contract_digest=f.active_contract_digest "
                                        "WHERE f.project_id=? AND f.bible_pair_digest=?",
                                        (project_id, cabal["bible_pair_digest"]))
                matches = [f for f in features if _feature_scope(f) == cabal["scope_digest"]]
                if len(matches) != 1:
                    raise CabalError("WRONG_FEATURE")
                return matches[0]
            feature = self._storage.read(locate)
            snapshot = self._snapshot(project_id, feature["feature_id"], feature["bible_pair_digest"])
            def read(tx: SQLView) -> Mapping[str, Any]:
                current = self._feature(tx, feature["feature_id"])
                if current["project_id"] != project_id:
                    raise CabalError("WRONG_PROJECT")
                self._current_pair(tx, project_id, snapshot)
                self._cabal(tx, project_id, cabal_id, current)
                roster = [r for r in self._roster(tx, project_id, cabal_id, now)
                          if self._has_qualification(tx, project_id, r, now)]
                return {"project_id": project_id, "feature_id": current["feature_id"], "cabal_id": cabal_id,
                        "charter_epoch": current["authority_epoch"], "bible_pair_digest": current["bible_pair_digest"],
                        "members": tuple(roster)}
            return Result("OK", "OK", self._storage.read(read))
        except CabalError as exc:
            return self._result_error(exc)
