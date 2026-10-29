"""R9 AuthorityPort: Bible-compatible autonomy, lease fences and owner-only effects.

The independent compliance verifier is mandatory. Neither worker-supplied
`checked_content` nor an AI-authored PASS result can authorize an effect.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from nomad_studio.bibles.api import _current_pair
from nomad_studio.bibles.model import (BibleError, Decision, Result, digest, hexsha, identifier,
                                        resolve_clauses, scope_path, source_path, strict)

_DELEGATED = frozenset(("FEATURE_BRANCH_WRITE", "FEATURE_BRANCH_COMMIT"))
_PROTECTED = frozenset(("PROTECTED_MAINLINE_MERGE", "RELEASE_PUBLISH", "BIBLE_ACTIVATE",
                        "BUDGET_EXPAND", "EXTERNAL_UNSCOPED_WRITE"))
_ACTIONS = _DELEGATED | _PROTECTED


def _intent(value: Any) -> dict[str, Any]:
    rec = strict(value, {"effect_id", "project_id", "feature_id", "action", "target_ref",
                         "bible_pair_digest", "feature_contract_digest", "scope_digest",
                         "payload_digest", "idempotency_key"}, {"owner_proof_ref", "expected_source_commit"})
    for key in ("effect_id", "project_id", "feature_id", "idempotency_key"):
        identifier(rec[key])
    for key in ("bible_pair_digest", "feature_contract_digest", "scope_digest", "payload_digest"):
        hexsha(rec[key])
    if type(rec["action"]) is not str or rec["action"] not in _ACTIONS or type(rec["target_ref"]) is not str or not rec["target_ref"] or len(rec["target_ref"]) > 16384:
        raise BibleError("INVALID_INPUT")
    if "owner_proof_ref" in rec:
        identifier(rec["owner_proof_ref"])
    if "expected_source_commit" in rec:
        if (type(rec["expected_source_commit"]) is not str or len(rec["expected_source_commit"]) not in (40, 64) or
                any(c not in "0123456789abcdef" for c in rec["expected_source_commit"])):
            raise BibleError("INVALID_INPUT")
    if rec["action"] in _PROTECTED and "owner_proof_ref" not in rec:
        raise BibleError("OWNER_ACTION_REQUIRED")
    return rec


def _feature(tx: Any, intent: Mapping[str, Any], contract_digest: str) -> Mapping[str, Any]:
    feature = tx.fetch_one("SELECT * FROM v1_feature_runs WHERE feature_id=? AND project_id=?",
                           (intent["feature_id"], intent["project_id"]))
    if feature is None:
        raise BibleError("WRONG_FEATURE")
    if feature["active_contract_digest"] != contract_digest or feature["bible_pair_digest"] != intent["bible_pair_digest"]:
        raise BibleError("REVISION_CONFLICT")
    contract = tx.fetch_one("SELECT * FROM v1_feature_contracts WHERE feature_id=? AND project_id=? AND contract_digest=?",
                            (intent["feature_id"], intent["project_id"], contract_digest))
    if contract is None or contract["bible_pair_digest"] != intent["bible_pair_digest"]:
        raise BibleError("REVISION_CONFLICT")
    if intent["scope_digest"] != contract["scope_digest"]:
        raise BibleError("SCOPE_EXCEEDED")
    return contract


def _lease(tx: Any, intent: Mapping[str, Any], contract: Mapping[str, Any], now_us: int) -> None:
    path = source_path(intent["target_ref"])
    if "expected_source_commit" not in intent:
        raise BibleError("SOURCE_CONFLICT")
    payload = json.loads(contract["payload_json"])
    repo = payload.get("repository_ref")
    branch = payload.get("feature_branch")
    if not isinstance(repo, str) or not isinstance(branch, str):
        raise BibleError("INVALID_INPUT")
    rows = tx.fetch_all("SELECT * FROM v1_source_leases WHERE feature_id=? AND repo=? AND branch=? AND active=1",
                        (intent["feature_id"], repo, branch))
    key = path.casefold()
    if not any(row["expires_at_us"] > now_us and
               row["expected_parent_commit"] == intent["expected_source_commit"] and
               source_path(row["resource_path"]).casefold() == row["resource_path_key"] and
               (key == row["resource_path_key"] or key.startswith(row["resource_path_key"].rstrip("/") + "/"))
               for row in rows):
        raise BibleError("LEASE_FENCED")


def _checked_receipt(record: Any, intent: Mapping[str, Any], requirements: list[dict[str, Any]]) -> dict[str, Any]:
    rec = strict(record, {"project_id", "feature_id", "bible_pair_digest", "subject_digest",
                          "clause_refs_checked", "violations", "semantic_review_ref",
                          "mechanical_check_refs", "result"})
    for key, origin in (("project_id", "project_id"), ("feature_id", "feature_id"),
                        ("bible_pair_digest", "bible_pair_digest")):
        if rec[key] != intent[origin]:
            raise BibleError("EVIDENCE_INVALID")
    if rec["subject_digest"] != intent["payload_digest"]:
        raise BibleError("EVIDENCE_INVALID")
    if rec["result"] not in ("PASS", "FAIL", "NOT_VERIFIED"):
        raise BibleError("EVIDENCE_INVALID")
    for name in ("clause_refs_checked", "violations", "mechanical_check_refs"):
        seq = rec[name]
        if not isinstance(seq, list) or len(seq) > 4096:
            raise BibleError("EVIDENCE_INVALID")
        for ref in seq:
            identifier(ref)
        if len(seq) != len(set(seq)):
            raise BibleError("EVIDENCE_INVALID")
    identifier(rec["semantic_review_ref"])
    required = {c["ref"] for c in requirements if c["kind"] == "REQUIREMENT"}
    if not required.issubset(rec["clause_refs_checked"]):
        raise BibleError("EVIDENCE_INVALID")
    if rec["result"] == "PASS" and rec["violations"]:
        raise BibleError("EVIDENCE_INVALID")
    if rec["result"] == "FAIL" and not rec["violations"]:
        raise BibleError("EVIDENCE_INVALID")
    # Every violation must be a known, applicable pinned clause.
    refs = {c["ref"] for c in requirements}
    if set(rec["violations"]) - refs:
        raise BibleError("EVIDENCE_INVALID")
    return rec


class AuthorityService:
    def __init__(self, state: Any, compliance_verifier: Any, owner_gateway: Any, clock_us: Any) -> None:
        self.state = state
        self.compliance_verifier = compliance_verifier
        self.owner_gateway = owner_gateway
        self.clock_us = clock_us

    def _context(self, tx: Any, intent: Mapping[str, Any], contract_digest: str,
                 scope: str, now_us: int) -> list[dict[str, Any]]:
        snap, clauses = _current_pair(tx, intent["project_id"])
        if snap["pair_digest"] != intent["bible_pair_digest"]:
            raise BibleError("STALE_BIBLE")
        contract = _feature(tx, intent, contract_digest)
        if intent["action"] in _DELEGATED:
            _lease(tx, intent, contract, now_us)
        return resolve_clauses(clauses, scope)

    def authorize_effect(self, effect_intent: Mapping[str, Any], *, bible_pair_digest: str,
                         contract_digest: str, checked_content: Mapping[str, Any]) -> Decision:
        ref = effect_intent.get("effect_id", "unknown") if isinstance(effect_intent, Mapping) else "unknown"
        try:
            intent = _intent(effect_intent)
            if intent["action"] in _PROTECTED:
                raise BibleError("OWNER_ACTION_REQUIRED")
            if (intent["bible_pair_digest"] != bible_pair_digest or
                    intent["feature_contract_digest"] != contract_digest):
                raise BibleError("STALE_BIBLE")
            checked = strict(checked_content, {"scope", "subject_digest", "review_input_ref"})
            if checked["subject_digest"] != intent["payload_digest"]:
                raise BibleError("EVIDENCE_INVALID")
            scope = scope_path(checked["scope"])
            if scope != source_path(intent["target_ref"]):
                raise BibleError("SCOPE_EXCEEDED")
            identifier(checked["review_input_ref"])
            now = self.clock_us()
            clauses = self.state.read(lambda tx: self._context(tx, intent, contract_digest, scope, now))
            # Trust boundary: independent verifier must evaluate exact referenced subject bytes.
            receipt = self.compliance_verifier(intent, checked, tuple(clauses))
            try:
                verified = _checked_receipt(receipt, intent, clauses)
            except BibleError as exc:
                raise BibleError("EVIDENCE_INVALID" if exc.code == "INVALID_INPUT" else exc.code, exc.refs) from exc
            # Recheck activation epoch, feature pin and lease *after* verifier I/O.
            post = self.state.read(lambda tx: self._context(tx, intent, contract_digest, scope, self.clock_us()))
            if post != clauses:
                raise BibleError("STALE_BIBLE")
            if verified["result"] != "PASS":
                raise BibleError("BIBLE_CONFLICT" if verified["result"] == "FAIL" else "EVIDENCE_INVALID",
                                 tuple(verified["violations"]))
            return Decision(True, "ALLOW", "OK", ref,
                            (verified["semantic_review_ref"],) + tuple(verified["mechanical_check_refs"]))
        except BibleError as exc:
            state = "OWNER_ACTION_REQUIRED" if exc.code == "OWNER_ACTION_REQUIRED" else "DENY"
            return Decision(False, state, exc.code, ref, exc.refs)

    def assess_bible_conflict(self, feature_id: str, decision_or_diff: Mapping[str, Any],
                              bible_pair_digest: str) -> Result:
        try:
            identifier(feature_id)
            request = strict(decision_or_diff, {"project_id", "scope", "subject_digest", "review_input_ref"})
            identifier(request["project_id"])
            identifier(request["review_input_ref"])
            hexsha(request["subject_digest"])
            scope = scope_path(request["scope"])
            def load(tx: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
                snap, clauses = _current_pair(tx, request["project_id"])
                if snap["pair_digest"] != bible_pair_digest:
                    raise BibleError("STALE_BIBLE")
                feature = tx.fetch_one("SELECT feature_id FROM v1_feature_runs WHERE feature_id=? AND project_id=? AND bible_pair_digest=?",
                                       (feature_id, request["project_id"], bible_pair_digest))
                if feature is None:
                    raise BibleError("WRONG_FEATURE")
                return snap, resolve_clauses(clauses, scope)
            _, clauses = self.state.read(load)
            intent = {"project_id": request["project_id"], "feature_id": feature_id,
                      "bible_pair_digest": bible_pair_digest, "payload_digest": request["subject_digest"]}
            try:
                receipt = _checked_receipt(self.compliance_verifier(intent, request, tuple(clauses)), intent, clauses)
            except BibleError as exc:
                raise BibleError("EVIDENCE_INVALID" if exc.code == "INVALID_INPUT" else exc.code, exc.refs) from exc
            _, after = self.state.read(load)
            if after != clauses:
                raise BibleError("STALE_BIBLE")
            if receipt["result"] == "FAIL":
                return Result("CONFLICT", "BIBLE_CONFLICT", record=receipt,
                              evidence_refs=tuple(receipt["violations"]))
            if receipt["result"] != "PASS":
                raise BibleError("EVIDENCE_INVALID")
            return Result("OK", "OK", record=receipt, evidence_refs=(receipt["semantic_review_ref"],))
        except BibleError as exc:
            return Result("CONFLICT" if exc.code == "STALE_BIBLE" else "REJECTED", exc.code,
                          evidence_refs=exc.refs)

    def authorize_protected_effect(self, effect_intent: Mapping[str, Any], owner_proof: Decision) -> Decision:
        ref = effect_intent.get("effect_id", "unknown") if isinstance(effect_intent, Mapping) else "unknown"
        try:
            intent = _intent(effect_intent)
            if intent["action"] not in _PROTECTED:
                raise BibleError("INVALID_INPUT")
            if intent["action"] == "BIBLE_ACTIVATE":
                # Activations must use BiblePort.activate_edition's epoch CAS path.
                raise BibleError("OWNER_ACTION_REQUIRED")
            subject = digest(intent)
            att = self.owner_gateway.authenticate(owner_proof, intent["action"], subject,
                                                  intent["project_id"], intent["effect_id"])
            if intent["owner_proof_ref"] != att["proof_ref"]:
                raise BibleError("EVIDENCE_INVALID")
            def write(tx: Any) -> None:
                project = tx.fetch_one("SELECT authority_epoch,owner_id FROM v1_projects WHERE project_id=?",
                                       (intent["project_id"],))
                if (project is None or project["authority_epoch"] != att["authority_epoch"] or
                        project["owner_id"] != att["owner_id"]):
                    raise BibleError("EVIDENCE_INVALID")
                snap, _ = _current_pair(tx, intent["project_id"])
                if snap["pair_digest"] != intent["bible_pair_digest"]:
                    raise BibleError("STALE_BIBLE")
                _feature(tx, intent, intent["feature_contract_digest"])
                prior = tx.fetch_one("SELECT decision_id FROM v1_owner_decisions WHERE proof_ref=?",
                                     (att["proof_ref"],))
                if prior is not None:
                    raise BibleError("EVIDENCE_INVALID")
                tx.execute("INSERT INTO v1_owner_decisions (project_id,decision_id,owner_id,action,subject_digest,"
                           "proof_ref,expires_at_us,consumed_at_us,payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                           (intent["project_id"], att["decision_id"], att["owner_id"], intent["action"],
                            subject, att["proof_ref"], att["expires_at_us"], att["now_us"],
                            json.dumps({"effect_id": intent["effect_id"],
                                        "project_authority_epoch": att["authority_epoch"]}, sort_keys=True)))
            self.state.transact(write)
            return Decision(True, "ALLOW", "OK", ref, (att["proof_ref"],))
        except BibleError as exc:
            state = "OWNER_ACTION_REQUIRED" if exc.code == "OWNER_ACTION_REQUIRED" else "DENY"
            return Decision(False, state, exc.code, ref, exc.refs)
