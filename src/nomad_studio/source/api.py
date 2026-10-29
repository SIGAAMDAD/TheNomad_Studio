"""WG-F: exact, independent Git source and CI proof; never publishes source.

All network readback happens before entering StateStore transactions. No module
other than the provided StateStore controls SQLite, and scheduler integration
uses only its frozen Component.query facade.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from importlib.resources import files
import json
import re
import time
import uuid
from typing import Any, Mapping

from nomad_studio.contracts.v1 import (
    CommandEnvelope, Component, DomainEvent, Outcome,
    QueryEnvelope, StateStore, VerifiedIdentity,
)
from .models import SourceReader, ProviderUnavailable
from .paths import PathError, canonical, pattern, change_paths, inspect_tree

# Project-neutral target identifiers are registered with owner authorization.
_TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
HARD_PROTECTED = (".github/**", ".gitmodules", ".git/**")
_SHA_RE = re.compile(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})\Z")
_REPO_RE = re.compile(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+\Z")
_BRANCH_RE = re.compile(r"refs/heads/[A-Za-z0-9_./-]+\Z")


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _digest(data: object) -> str:
    return sha256(_json(data).encode("utf-8")).hexdigest()


def _sha(value: object) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise InputError("INVALID_SCHEMA", "invalid source object SHA")
    return value.lower()


def _keys(value: object, allowed: set[str], required: set[str] = frozenset()) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not required.issubset(value) or not set(value).issubset(allowed):
        raise InputError("INVALID_SCHEMA", "unknown or missing request fields")
    return value


def _required_text(value: object, max_len: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise InputError("INVALID_SCHEMA", "required bounded text missing")
    return value


def _integer(value: object, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise InputError("INVALID_SCHEMA", "invalid generation/epoch")
    return value


def _target(value: object) -> str:
    if not isinstance(value, str) or not _TARGET_RE.fullmatch(value) or ".." in value:
        raise InputError("INVALID_SCOPE", "invalid source-target identifier")
    return value


class InputError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _policy(raw: object) -> tuple[str, str]:
    data = _keys(raw, {"owned_paths", "protected_paths", "ci_workflows", "max_tree_entries"},
                 {"owned_paths", "protected_paths"})
    def patterns(key: str) -> list[str]:
        values = data[key]
        if not isinstance(values, list) or len(values) > 256:
            raise InputError("INVALID_SCHEMA", "invalid policy path array")
        return sorted(set(pattern(item) for item in values))
    owned = patterns("owned_paths")
    if not owned:
        raise InputError("INVALID_SCOPE", "empty source ownership policy")
    protected = sorted(set(patterns("protected_paths")) | set(HARD_PROTECTED))
    ci = data.get("ci_workflows", [])
    if not isinstance(ci, list) or len(ci) > 64 or not all(isinstance(i, str) and 0 < len(i) <= 200 for i in ci):
        raise InputError("INVALID_SCHEMA", "invalid CI workflow allowlist")
    limit = _integer(data.get("max_tree_entries", 200000), 1)
    if limit > 500000:
        raise InputError("INVALID_SCHEMA", "tree limit exceeds hard ceiling")
    normalized = {"owned_paths": owned, "protected_paths": protected,
                  "ci_workflows": sorted(set(ci)), "max_tree_entries": limit}
    return _json(normalized), _digest(normalized)




class SourceComponent:
    name = "source"

    def __init__(self, deps: Mapping[str, object]):
        self._store: StateStore | None = deps.get("store")  # type: ignore[assignment]
        self._reader: SourceReader | None = deps.get("source_reader")  # type: ignore[assignment]
        self._scheduler: Component | None = deps.get("scheduler")  # type: ignore[assignment]
        self._clock = deps.get("clock_us", lambda: time.time_ns() // 1000)

    def _now(self) -> int:
        return int(self._clock())  # type: ignore[operator]

    def _access(self, identity: VerifiedIdentity, scopes: set[str]) -> None:
        if not isinstance(identity, VerifiedIdentity) or not identity.principal_id or not identity.credential_id or not identity.issuer_id or not identity.authenticated_via:
            raise InputError("IDENTITY_UNVERIFIED", "invalid verified gateway identity")
        if identity.expires_at_us <= self._now():
            raise InputError("EXPIRED_GRANT", "identity grant expired")
        if not scopes.issubset(identity.scopes):
            raise InputError("INVALID_SCOPE", "required gateway-verified scopes unavailable")
        if "owner:approve" in scopes and identity.authenticated_via.lower() in {"prompt", "unverified", "text", "worker_claim"}:
            raise InputError("IDENTITY_UNVERIFIED", "prompt-carried identity cannot perform ownership policy changes")

    def _reader_guard(self, needs_ci: bool = False) -> SourceReader:
        reader = self._reader
        if reader is None:
            raise InputError("NOT_CONFIGURED", "independent source reader missing")
        scopes = getattr(reader, "credential_scopes", frozenset())
        if not isinstance(scopes, (frozenset, set)) or "repo:read" not in scopes or (needs_ci and "ci:read" not in scopes):
            raise InputError("UNTRUSTED_CONNECTOR", "missing source read-only credential scope")
        if any("write" in s or "admin" in s or "publish" in s for s in scopes) or getattr(reader, "independent_readback", None) is not True:
            raise InputError("UNTRUSTED_CONNECTOR", "publisher credential cannot attest independent readback")
        return reader

    def _require_store(self) -> StateStore:
        if self._store is None:
            raise InputError("NOT_CONFIGURED", "shared state store missing")
        return self._store

    def _load_target(self, key: str, *, allow_disabled: bool = False) -> Mapping[str, Any]:
        store = self._require_store()
        row = store.read(lambda db: db.fetch_one("SELECT * FROM source_targets WHERE target_key=?", (key,)))
        if row is None or (not allow_disabled and int(row["enabled"]) != 1):
            raise InputError("NOT_CONFIGURED", "current source-target mapping not registered or disabled")
        return dict(row)

    def _provider(self, row: Mapping[str, Any], with_tree: bool = True) -> tuple[str, str, tuple[Any, ...]]:
        reader = self._reader_guard()
        repo, ref = row["repository"], row["branch_ref"]
        try:
            observed = reader.get_ref(repo, ref)
            if observed.repository != repo or observed.ref != ref:
                raise ProviderUnavailable("provider returned different repository or ref")
            head = _sha(observed.commit_sha)
            commit = reader.get_commit(repo, head)
            if commit.repository != repo or _sha(commit.commit_sha) != head:
                raise ProviderUnavailable("provider commit identity mismatch")
            tree_sha = _sha(commit.tree_sha)
            if not with_tree:
                return head, tree_sha, ()
            maximum = int(json.loads(row["policy_json"])["max_tree_entries"])
            tree = reader.get_tree(repo, tree_sha, max_entries=maximum)
            if tree.repository != repo or _sha(tree.tree_sha) != tree_sha or tree.complete is not True or len(tree.entries) > maximum:
                raise ProviderUnavailable("tree incomplete or mismatched")
            for item in tree.entries:
                _sha(item.object_sha)
                if item.kind not in ("blob", "tree", "symlink", "submodule"):
                    raise ProviderUnavailable("unrecognized Git tree entry kind")
                if item.kind == "symlink" and item.mode != "120000":
                    raise ProviderUnavailable("inconsistent symlink mode")
                if item.kind == "submodule" and item.mode != "160000":
                    raise ProviderUnavailable("inconsistent submodule mode")
                if item.kind == "blob" and item.mode not in ("100644", "100755"):
                    raise ProviderUnavailable("inconsistent blob mode")
                if item.kind == "tree" and item.mode != "040000":
                    raise ProviderUnavailable("inconsistent tree mode")
            return head, tree_sha, tree.entries
        except (ProviderUnavailable, OSError, AttributeError, ValueError, KeyError, TypeError) as exc:
            raise ProviderUnavailable("independent provider source readback unavailable or invalid") from exc

    def _confirm_head(self, row: Mapping[str, Any], expected: str) -> None:
        """Repeat authenticated ref readback after scheduler/CI checks, before proof commit.

        This closes the common HEAD-moves-during-fence window. The downstream
        publisher still MUST use atomic expected-ref compare-and-swap, since a
        remote branch can change after this proof is persisted.
        """
        reader = self._reader_guard()
        try:
            observation = reader.get_ref(row["repository"], row["branch_ref"])
            if observation.repository != row["repository"] or observation.ref != row["branch_ref"]:
                raise ProviderUnavailable("ref identity mismatch on final readback")
            fresh = _sha(observation.commit_sha)
        except (ProviderUnavailable, OSError, AttributeError, ValueError, TypeError) as exc:
            raise ProviderUnavailable("final source ref readback unavailable") from exc
        if fresh != expected:
            raise InputError("REBASE_REQUIRED", "source HEAD moved during verification")

    def _fence(self, command: CommandEnvelope, identity: VerifiedIdentity, row: Mapping[str, Any],
               claimed: Mapping[str, Any], observed_sha: str) -> Mapping[str, Any]:
        scheduler = self._scheduler
        if scheduler is None:
            raise InputError("NOT_CONFIGURED", "scheduler claim snapshot adapter absent")
        if not command.job_id or command.assignment_epoch is None or not command.worker_id:
            raise InputError("INVALID_SCHEMA", "job ID, worker ID and assignment epoch required")
        response = scheduler.query(QueryEnvelope(
            version="NOMAD/QUERY/1", request_id=str(uuid.uuid4()), domain="scheduler",
            operation="claim", job_id=command.job_id, worker_id=command.worker_id,
            payload={"target": row["target_key"], "repository": row["repository"], "ref": row["branch_ref"]},
        ), identity)
        if response.state != "ACCEPTED":
            raise InputError("STALE_LEASE" if response.state in ("CONFLICT", "DENIED") else "NOT_CONFIGURED",
                             "current scheduler claim unavailable")
        snapshot = response.payload
        expected = {"principal_id", "worker_id", "job_id", "assignment_epoch", "lease_generation",
                    "authority_epoch", "mapping_epoch", "policy_digest", "resource_scope", "expected_head", "expires_at_us"}
        if not isinstance(snapshot, Mapping) or not expected.issubset(snapshot):
            raise InputError("INVALID_SCHEMA", "scheduler has no authoritative source fence snapshot")
        if snapshot["principal_id"] != identity.principal_id or snapshot["worker_id"] != command.worker_id or snapshot["job_id"] != command.job_id:
            raise InputError("STALE_ASSIGNMENT", "assignment is not owned by authenticated principal")
        if _integer(snapshot["authority_epoch"]) != identity.current_authority_epoch:
            raise InputError("STALE_ASSIGNMENT", "gateway authority epoch differs from scheduler")
        if _integer(snapshot["assignment_epoch"], 1) != _integer(command.assignment_epoch, 1):
            raise InputError("STALE_ASSIGNMENT", "assignment epoch changed")
        if _integer(snapshot["lease_generation"], 1) != _integer(claimed["lease_generation"], 1):
            raise InputError("STALE_LEASE", "lease generation changed")
        if _integer(snapshot["mapping_epoch"], 1) != int(row["mapping_epoch"]):
            raise InputError("REBASE_REQUIRED", "repository mapping changed")
        if snapshot["policy_digest"] != row["policy_digest"] or claimed["policy_digest"] != row["policy_digest"]:
            raise InputError("REBASE_REQUIRED", "ownership policy changed")
        if _sha(snapshot["expected_head"]) != observed_sha or _sha(claimed["base_sha"]) != observed_sha or _sha(command.source_sha) != observed_sha:
            raise InputError("REBASE_REQUIRED", "source HEAD moved; explicit rebase and new claim required")
        if _integer(snapshot["expires_at_us"], 1) <= self._now():
            raise InputError("STALE_LEASE", "source lease expired")
        assigned_scope = snapshot["resource_scope"]
        if not isinstance(assigned_scope, list) or not assigned_scope or len(assigned_scope) > 256:
            raise InputError("INVALID_SCHEMA", "scheduler returned invalid resource scope")
        validated = sorted(set(pattern(item) for item in assigned_scope))
        incoming = claimed["resource_scope"]
        if not isinstance(incoming, list) or sorted(set(pattern(item) for item in incoming)) != validated:
            raise InputError("INVALID_SCOPE", "untrusted resource scope does not match live assignment")
        return {"assignment_epoch": command.assignment_epoch, "lease_generation": claimed["lease_generation"],
                "authority_epoch": snapshot["authority_epoch"], "mapping_epoch": row["mapping_epoch"],
                "policy_digest": row["policy_digest"], "resource_scope": validated, "job_id": command.job_id,
                "principal_id": identity.principal_id}

    def _proof_store(self, command: CommandEnvelope, identity: VerifiedIdentity,
                     row: Mapping[str, Any], kind: str, subject_sha: str,
                     body: Mapping[str, Any]) -> Outcome:
        digest = _digest(body)
        receipt = str(uuid.uuid4())
        operation_digest = _digest({"operation": command.operation, "principal": identity.principal_id,
                                    "job": command.job_id, "epoch": command.assignment_epoch,
                                    "worker": command.worker_id, "source_sha": command.source_sha,
                                    "payload": command.payload})
        result = Outcome(state="ACCEPTED", code="SOURCE_PROOF_VERIFIED", receipt_id=receipt, payload=body)
        def apply(tx: Any) -> Outcome:
            cached = tx.fetch_one("SELECT * FROM source_operations WHERE idempotency_key=?", (command.idempotency_key,))
            if cached:
                if cached["request_digest"] != operation_digest:
                    return Outcome(state="CONFLICT", code="IDEMPOTENCY_CONFLICT")
                old = json.loads(cached["outcome_json"])
                return Outcome(**old)
            tx.execute("INSERT INTO source_proofs VALUES (?,?,?,?,?,?,?,?)",
                       (receipt, row["target_key"], kind, subject_sha, digest,
                        _json(body), self._now(), identity.principal_id))
            event = DomainEvent(event_id=str(uuid.uuid4()), domain="source", kind=kind,
                                actor_principal_id=identity.principal_id, issued_at_us=self._now(),
                                correlation_id=command.request_id, job_id=command.job_id,
                                assignment_epoch=command.assignment_epoch, payload_sha256=digest,
                                payload={"receipt_id": receipt, "target": row["target_key"], "subject_sha": subject_sha})
            tx.append_event(event)
            tx.execute("INSERT INTO source_operations VALUES (?,?,?,?)",
                       (command.idempotency_key, operation_digest, receipt, _json(asdict(result))))
            return result
        return self._require_store().transact(apply, idempotency_key=command.idempotency_key)

    def _check(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        ci = command.operation == "readback_ci"
        allowed = {"target", "base_sha", "lease_generation", "policy_digest", "resource_scope", "changes"}
        if ci:
            allowed |= {"run_id"}
        data = _keys(command.payload, allowed, allowed - ({"changes"} | ({"run_id"} if not ci else set())))
        if "changes" in data and not isinstance(data["changes"], list):
            raise InputError("INVALID_SCHEMA", "changes must be list")
        if ci and "changes" in data:
            raise InputError("INVALID_SCHEMA", "CI readback does not accept path mutations")
        row = self._load_target(_target(data["target"]))
        head, tree_sha, entries = self._provider(row, with_tree=not ci)
        fence = self._fence(command, identity, row, data, head)
        if ci:
            reader = self._reader_guard(needs_ci=True)
            run_id = _required_text(data["run_id"], 160)
            try:
                observed = reader.get_ci(row["repository"], run_id)
                if observed.repository != row["repository"] or observed.run_id != run_id:
                    raise ProviderUnavailable("CI run does not match requested repository or ID")
                ci_sha = _sha(observed.head_sha)
            except (ProviderUnavailable, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ProviderUnavailable("independent CI readback unavailable") from exc
            if ci_sha != head:
                raise InputError("SOURCE_SHA_MISMATCH", "CI run is not for exact current HEAD")
            policy = json.loads(row["policy_json"])
            if policy["ci_workflows"] and observed.workflow not in policy["ci_workflows"]:
                raise InputError("INVALID_SCOPE", "CI workflow not allowed by source policy")
            if observed.status != "completed" or observed.conclusion != "success":
                return Outcome(state="BLOCKED", code="QA_PENDING",
                               payload={"target": row["target_key"], "head_sha": head, "ci_run_id": run_id})
            self._confirm_head(row, head)
            body = {"target": row["target_key"], "repository": row["repository"], "ref": row["branch_ref"],
                    "subject_sha": head, "tree_sha": tree_sha, "ci_run_id": run_id,
                    "workflow": observed.workflow, "status": observed.status, "conclusion": observed.conclusion,
                    "fence": fence}
            return self._proof_store(command, identity, row, "CI_READBACK_VERIFIED", head, body)
        paths, changes = change_paths(data.get("changes", []))
        policy = json.loads(row["policy_json"])
        inspect_tree(changes, entries, policy["owned_paths"], policy["protected_paths"], fence["resource_scope"])
        self._confirm_head(row, head)
        selected = {entry.path: entry.object_sha for entry in entries if entry.path in paths}
        body = {"target": row["target_key"], "repository": row["repository"], "ref": row["branch_ref"],
                "subject_sha": head, "tree_sha": tree_sha, "verified_paths": list(paths),
                "observed_path_object_shas": selected, "changes": list(changes), "fence": fence}
        if command.operation == "check_paths":
            return Outcome(state="ACCEPTED", code="PATHS_VERIFIED", payload=body)
        return self._proof_store(command, identity, row, "SOURCE_BASE_VERIFIED", head, body)

    def _resolve(self, payload: Mapping[str, Any]) -> Outcome:
        data = _keys(payload, {"target"}, {"target"})
        row = self._load_target(_target(data["target"]))
        head, tree_sha, entries = self._provider(row)
        return Outcome(state="ACCEPTED", code="SOURCE_RESOLVED", receipt_id=_digest({
            "target": row["target_key"], "head": head, "tree": tree_sha, "policy": row["policy_digest"]}),
            payload={"target": row["target_key"], "repository": row["repository"], "ref": row["branch_ref"],
                     "head_sha": head, "tree_sha": tree_sha, "mapping_epoch": row["mapping_epoch"],
                     "policy_digest": row["policy_digest"], "tree_entries": len(entries),
                     "symlinks": sum(x.kind == "symlink" for x in entries),
                     "submodules": sum(x.kind == "submodule" for x in entries),
                     "protected_paths": json.loads(row["policy_json"])["protected_paths"]})

    def _protect(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        self._access(identity, {"source:admin", "owner:approve"})
        data = _keys(command.payload,
                     {"action", "target", "repository", "ref", "policy", "expected_mapping_epoch"},
                     {"action", "target", "expected_mapping_epoch"})
        action, key = data["action"], _target(data["target"])
        epoch = _integer(data["expected_mapping_epoch"])
        if action == "upsert":
            _keys(data, {"action", "target", "repository", "ref", "policy", "expected_mapping_epoch"},
                  {"action", "target", "repository", "ref", "policy", "expected_mapping_epoch"})
            repo, ref = _required_text(data["repository"], 200), _required_text(data["ref"], 250)
            if (_REPO_RE.fullmatch(repo) is None or any(part in (".", "..") or part.endswith(".lock") for part in repo.split("/"))
                    or _BRANCH_RE.fullmatch(ref) is None or any(part in ("", ".", "..") or part.endswith(".lock") for part in ref.split("/")[2:])
                    or ".." in ref or ref.endswith("/") or "@{" in ref):
                raise InputError("INVALID_SCHEMA", "invalid exact repository or branch ref")
            policy_json, policy_digest = _policy(data["policy"])
            tmp = {"repository": repo, "branch_ref": ref, "policy_json": policy_json}
            head, tree_sha, _ = self._provider(tmp, with_tree=False)
            enabled = 1
        elif action == "disable":
            if set(data) != {"action", "target", "expected_mapping_epoch"}:
                raise InputError("INVALID_SCHEMA", "disable has no mapping or policy fields")
            repo = ref = policy_json = policy_digest = head = tree_sha = ""
            enabled = 0
        else:
            raise InputError("INVALID_SCHEMA", "unknown protection action")
        operation_digest = _digest({"action": command.operation, "principal": identity.principal_id, "payload": command.payload})
        receipt = str(uuid.uuid4())
        def apply(tx: Any) -> Outcome:
            previous = tx.fetch_one("SELECT * FROM source_operations WHERE idempotency_key=?", (command.idempotency_key,))
            if previous:
                if previous["request_digest"] != operation_digest:
                    return Outcome(state="CONFLICT", code="IDEMPOTENCY_CONFLICT")
                return Outcome(**json.loads(previous["outcome_json"]))
            old = tx.fetch_one("SELECT * FROM source_targets WHERE target_key=?", (key,))
            observed_epoch = int(old["mapping_epoch"]) if old else 0
            if observed_epoch != epoch:
                return Outcome(state="CONFLICT", code="STALE_ASSIGNMENT")
            if action == "disable":
                if old is None:
                    return Outcome(state="BLOCKED", code="NOT_CONFIGURED")
                tx.execute("UPDATE source_targets SET enabled=0, mapping_epoch=?, configured_by=? WHERE target_key=?",
                           (epoch + 1, identity.principal_id, key))
            else:
                tx.execute("INSERT INTO source_targets VALUES (?,?,?,?,?,?,?,?,?,?) "
                           "ON CONFLICT(target_key) DO UPDATE SET repository=excluded.repository, branch_ref=excluded.branch_ref, "
                           "policy_json=excluded.policy_json, policy_digest=excluded.policy_digest, "
                           "mapping_epoch=excluded.mapping_epoch, enabled=excluded.enabled, "
                           "configured_by=excluded.configured_by, verified_head=excluded.verified_head, "
                           "verified_tree=excluded.verified_tree",
                           (key, repo, ref, policy_json, policy_digest, epoch + 1, enabled,
                            identity.principal_id, head, tree_sha))
            body = {"target": key, "mapping_epoch": epoch + 1, "action": action}
            outcome = Outcome(state="ACCEPTED", code="SOURCE_POLICY_UPDATED", receipt_id=receipt, payload=body)
            tx.append_event(DomainEvent(event_id=str(uuid.uuid4()), domain="source", kind="SOURCE_POLICY_UPDATED",
                                        actor_principal_id=identity.principal_id, issued_at_us=self._now(),
                                        correlation_id=command.request_id, payload_sha256=_digest(body), payload=body))
            tx.execute("INSERT INTO source_operations VALUES (?,?,?,?)",
                       (command.idempotency_key, operation_digest, receipt, _json(asdict(outcome))))
            return outcome
        return self._require_store().transact(apply, idempotency_key=command.idempotency_key)

    def execute(self, command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome:
        try:
            if not isinstance(command, CommandEnvelope) or command.version != "NOMAD/CMD/1" or command.domain != "source" or command.operation not in {"resolve", "verify_base", "check_paths", "readback_ci", "protect"} or not isinstance(command.idempotency_key, str) or not 0 < len(command.idempotency_key) <= 200 or not isinstance(command.request_id, str) or not 0 < len(command.request_id) <= 200:
                raise InputError("MALFORMED_REQUEST", "invalid source command envelope")
            if command.operation == "protect":
                return self._protect(command, identity)
            self._access(identity, {"source:verify"} if command.operation != "resolve" else {"source:read"})
            if command.operation == "resolve":
                return self._resolve(command.payload)
            return self._check(command, identity)
        except (InputError, PathError) as exc:
            state = "BLOCKED" if exc.code in ("NOT_CONFIGURED", "QA_PENDING") else "DENIED"
            if exc.code.startswith("STALE") or exc.code == "REBASE_REQUIRED":
                state = "CONFLICT"
            return Outcome(state=state, code=exc.code)
        except ProviderUnavailable:
            return Outcome(state="UNKNOWN", code="PROVIDER_UNKNOWN")
        except Exception:
            return Outcome(state="UNKNOWN", code="PERSISTENCE_UNKNOWN")

    def query(self, query: QueryEnvelope, identity: VerifiedIdentity) -> Outcome:
        try:
            if not isinstance(query, QueryEnvelope) or query.version != "NOMAD/QUERY/1" or query.domain != "source" or query.operation not in {"resolve", "protect"} or not isinstance(query.request_id, str) or not 0 < len(query.request_id) <= 200:
                raise InputError("MALFORMED_REQUEST", "invalid source query envelope")
            self._access(identity, {"source:read"})
            if query.operation == "resolve":
                return self._resolve(query.payload)
            data = _keys(query.payload, {"target"}, {"target"})
            row = self._load_target(_target(data["target"]), allow_disabled=True)
            return Outcome(state="ACCEPTED", code="SOURCE_POLICY", payload={
                "target": row["target_key"], "repository": row["repository"], "ref": row["branch_ref"],
                "mapping_epoch": row["mapping_epoch"], "policy_digest": row["policy_digest"],
                "policy": json.loads(row["policy_json"]), "enabled": bool(row["enabled"])})
        except (InputError, PathError) as exc:
            return Outcome(state="BLOCKED" if exc.code == "NOT_CONFIGURED" else "DENIED", code=exc.code)
        except ProviderUnavailable:
            return Outcome(state="UNKNOWN", code="PROVIDER_UNKNOWN")
        except Exception:
            return Outcome(state="UNKNOWN", code="PERSISTENCE_UNKNOWN")


def build_component(deps: Mapping[str, object]) -> Component:
    return SourceComponent(deps)
