"""R9 BiblePort, backed by P2-injected CAB-D-only SQLite capabilities.

`state` supplies read(callback) and transact(callback); P2 enforces per-table
writer capabilities. `source_reader` and `owner_gateway` are trusted providers.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .model import (BibleError, Decision, Result, canonical, digest, identifier,
                    resolve_clauses, scope_path, source_closure, validate_edition)


def _failure(exc: BibleError) -> Result:
    status = ("NOT_FOUND" if exc.code == "NOT_FOUND" else
              "CONFLICT" if exc.code in {"STALE_BIBLE", "REVISION_CONFLICT", "SOURCE_CONFLICT"} else
              "REJECTED")
    return Result(status, exc.code, evidence_refs=exc.refs)


def _current_pair(tx: Any, project_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = tx.fetch_all("SELECT * FROM v1_bible_editions WHERE project_id=? AND active=1", (project_id,))
    selected = {row["kind"]: row for row in rows}
    if set(selected) != {"DESIGN", "TECHNICAL"} or len(rows) != 2:
        raise BibleError("STALE_BIBLE")
    des, tech = selected["DESIGN"], selected["TECHNICAL"]
    snap = {"project_id": project_id, "design_edition_id": des["edition_id"],
            "technical_edition_id": tech["edition_id"],
            "design_manifest_digest": des["manifest_digest"],
            "technical_manifest_digest": tech["manifest_digest"],
            "design_epoch": des["epoch"], "technical_epoch": tech["epoch"]}
    snap["pair_digest"] = digest(snap)
    clauses = []
    for row in (des, tech):
        doc = json.loads(row["payload_json"])
        for clause in doc["clauses"]:
            clauses.append({**clause, "edition_id": row["edition_id"],
                            "bible_kind": row["kind"],
                            "ref": row["edition_id"] + ":" + clause["clause_id"]})
    refs = [c["ref"] for c in clauses]
    if len(refs) != len(set(refs)):
        raise BibleError("INVALID_INPUT")
    return snap, clauses


class BibleRegistry:
    """Implements all frozen BiblePort methods without owning app.py or DDL."""

    def __init__(self, state: Any, source_reader: Any, owner_gateway: Any) -> None:
        self.state = state
        self.source_reader = source_reader
        self.owner_gateway = owner_gateway

    def active_pair(self, project_id: str) -> dict[str, Any]:
        """Trusted CAB-I read adapter; uses the same active-pair read as the port."""
        identifier(project_id)
        pair = self.snapshot_active_pair(project_id, {'project_id': project_id})
        if pair.status != 'OK' or not isinstance(pair.record, Mapping):
            raise BibleError(pair.code)
        return dict(pair.record)

    def excerpt(self, edition_id: str, clause_id: str) -> dict[str, str]:
        """Return immutable clause text from source bytes, never the submitted request."""
        identifier(edition_id)
        identifier(clause_id)
        def query(tx: Any) -> dict[str, Any]:
            row = tx.fetch_one('SELECT * FROM v1_bible_editions WHERE edition_id=? AND active=1', (edition_id,))
            if row is None: raise BibleError('STALE_BIBLE')
            clause = tx.fetch_one('SELECT * FROM v1_bible_clauses WHERE edition_id=? AND clause_id=?',
                                  (edition_id, clause_id))
            if clause is None: raise BibleError('NOT_FOUND')
            return {'edition': dict(row), 'clause': dict(clause)}
        observed = self.state.read(query)
        edition, row = observed['edition'], observed['clause']
        doc = json.loads(edition['payload_json'])
        trusted = self.source_reader(doc['repo'], doc['source_commit'], {'edition_id': edition_id})
        # Re-verify a complete manifest and every source byte before reading text.
        source_closure({**doc, 'manifest_digest': edition['manifest_digest']}, trusted)
        item = next((c for c in doc['clauses'] if c['clause_id'] == clause_id), None)
        if item is None or item['text_digest'] != row['content_digest']:
            raise BibleError('EVIDENCE_INVALID')
        raw = trusted['files'][item['source_ref']]
        text = raw if __import__('hashlib').sha256(raw).hexdigest() == item['text_digest'] else trusted.get('clause_bytes', {}).get(clause_id)
        if (not isinstance(text, bytes) or __import__('hashlib').sha256(text).hexdigest() != item['text_digest']):
            raise BibleError('EVIDENCE_INVALID')
        return {'kind': row['kind'], 'scope': row['scope'], 'text': text.decode('utf-8')}

    def propose_edition(self, edition: Mapping[str, Any], source_readback: Mapping[str, Any]) -> Result:
        try:
            doc = validate_edition(edition)
            if "owner_activation_ref" in doc or not isinstance(source_readback, Mapping):
                raise BibleError("INVALID_INPUT")
            # Provider readback is deliberately outside SQLite writer transactions.
            observed = self.source_reader(doc["repo"], doc["source_commit"], dict(source_readback))
            evidence = source_closure(doc, observed)
            def write(tx: Any) -> None:
                proj = tx.fetch_one("SELECT project_id FROM v1_projects WHERE project_id=?", (doc["project_id"],))
                if proj is None:
                    raise BibleError("WRONG_PROJECT")
                existing = tx.fetch_one("SELECT edition_id FROM v1_bible_editions WHERE edition_id=?", (doc["edition_id"],))
                if existing is not None:
                    raise BibleError("REVISION_CONFLICT")
                latest = tx.fetch_one("SELECT MAX(epoch) AS epoch FROM v1_bible_editions WHERE project_id=? AND kind=?",
                                      (doc["project_id"], doc["kind"]))
                if doc["epoch"] <= (latest["epoch"] if latest and latest["epoch"] is not None else 0):
                    raise BibleError("STALE_BIBLE")
                tx.execute("INSERT INTO v1_bible_editions (project_id,kind,edition_id,epoch,manifest_digest,"
                           "source_repo,source_commit,owner_activation_ref,active,payload_json)"
                           " VALUES (?,?,?,?,?,?,?,NULL,0,?)",
                           (doc["project_id"], doc["kind"], doc["edition_id"], doc["epoch"],
                            doc["manifest_digest"], doc["repo"], doc["source_commit"], canonical(doc).decode()))
                for clause in doc["clauses"]:
                    tx.execute("INSERT INTO v1_bible_clauses (edition_id,clause_id,kind,scope,content_digest,priority)"
                               " VALUES (?,?,?,?,?,?)", (doc["edition_id"], clause["clause_id"],
                                clause["kind"], clause["scope"], clause["text_digest"], clause["precedence"]))
            self.state.transact(write)
            return Result("ACCEPTED", "OK", record=doc, receipt_ref=evidence, evidence_refs=(evidence,))
        except BibleError as exc:
            return _failure(exc)

    def activate_edition(self, edition_id: str, owner_proof: Decision, expected_active_epoch: int) -> Result:
        try:
            identifier(edition_id)
            if type(expected_active_epoch) is not int or expected_active_epoch < 0:
                raise BibleError("INVALID_INPUT")
            def read(tx: Any) -> dict[str, Any]:
                row = tx.fetch_one("SELECT * FROM v1_bible_editions WHERE edition_id=?", (edition_id,))
                if row is None:
                    raise BibleError("NOT_FOUND")
                return dict(row)
            edition = self.state.read(read)
            if edition["active"]:
                raise BibleError("REVISION_CONFLICT")
            subject_digest = digest({"action": "BIBLE_ACTIVATE", "project_id": edition["project_id"],
                                     "edition_id": edition_id, "kind": edition["kind"],
                                     "epoch": edition["epoch"], "manifest_digest": edition["manifest_digest"]})
            # The trusted gateway re-authenticates the proof against exact action+subject.
            attestation = self.owner_gateway.authenticate(owner_proof, "BIBLE_ACTIVATE", subject_digest,
                                                          edition["project_id"], edition_id)
            def write(tx: Any) -> None:
                project = tx.fetch_one("SELECT authority_epoch,owner_id FROM v1_projects WHERE project_id=?",
                                       (edition["project_id"],))
                if (project is None or project["authority_epoch"] != attestation["authority_epoch"] or
                        project["owner_id"] != attestation["owner_id"]):
                    raise BibleError("EVIDENCE_INVALID")
                latest = tx.fetch_one("SELECT epoch FROM v1_bible_editions WHERE project_id=? AND kind=? AND active=1",
                                      (edition["project_id"], edition["kind"]))
                epoch = latest["epoch"] if latest is not None else 0
                if epoch != expected_active_epoch or edition["epoch"] <= epoch:
                    raise BibleError("STALE_BIBLE")
                proposed = tx.fetch_one("SELECT active,epoch,manifest_digest FROM v1_bible_editions WHERE edition_id=?",
                                        (edition_id,))
                if (proposed is None or proposed["active"] or proposed["epoch"] != edition["epoch"] or
                        proposed["manifest_digest"] != edition["manifest_digest"]):
                    raise BibleError("REVISION_CONFLICT")
                if tx.fetch_one("SELECT decision_id FROM v1_owner_decisions WHERE proof_ref=?",
                                (attestation["proof_ref"],)) is not None:
                    raise BibleError("EVIDENCE_INVALID")
                tx.execute("INSERT INTO v1_owner_decisions (project_id,decision_id,owner_id,action,subject_digest,"
                           "proof_ref,expires_at_us,consumed_at_us,payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                           (edition["project_id"], attestation["decision_id"], attestation["owner_id"],
                            "BIBLE_ACTIVATE", subject_digest, attestation["proof_ref"],
                            attestation["expires_at_us"], attestation["now_us"],
                            canonical({"edition_id": edition_id, "project_authority_epoch": attestation["authority_epoch"]}).decode()))
                tx.execute("UPDATE v1_bible_editions SET active=0 WHERE project_id=? AND kind=? AND active=1",
                           (edition["project_id"], edition["kind"]))
                doc = json.loads(edition["payload_json"])
                doc["owner_activation_ref"] = attestation["proof_ref"]
                tx.execute("UPDATE v1_bible_editions SET active=1,owner_activation_ref=?,payload_json=? WHERE edition_id=?",
                           (attestation["proof_ref"], canonical(doc).decode(), edition_id))
            self.state.transact(write)
            return Result("OK", "OK", record={"edition_id": edition_id, "project_id": edition["project_id"],
                                            "active_epoch": edition["epoch"]},
                          receipt_ref=attestation["proof_ref"], evidence_refs=(attestation["proof_ref"],))
        except BibleError as exc:
            return _failure(exc)

    def snapshot_active_pair(self, project_id: str, feature_scope: Mapping[str, Any]) -> Result:
        try:
            identifier(project_id)
            if not isinstance(feature_scope, Mapping) or feature_scope.get("project_id") != project_id:
                raise BibleError("WRONG_PROJECT")
            snap, _ = self.state.read(lambda tx: _current_pair(tx, project_id))
            return Result("OK", "OK", record=snap, evidence_refs=(snap["design_edition_id"],
                                                                    snap["technical_edition_id"]))
        except BibleError as exc:
            return _failure(exc)

    def get_clause_bundle(self, bible_pair_digest: str, scope: Mapping[str, Any]) -> Result:
        try:
            if not isinstance(scope, Mapping) or set(scope) not in (
                {"project_id", "scope"}, {"project_id", "feature_id"}):
                raise BibleError("INVALID_INPUT")
            project = identifier(scope["project_id"])
            # Feature intake has not resolved individual asset paths yet.
            # Return full metadata for an authorized project's pair; content
            # must be verified from CAB-I's immutable, byte-checked excerpts.
            feature_scope = "feature_id" in scope
            if feature_scope:
                identifier(scope["feature_id"])
                path = "*"
            else:
                path = scope_path(scope["scope"])
            snap, clauses = self.state.read(lambda tx: _current_pair(tx, project))
            if snap["pair_digest"] != bible_pair_digest:
                raise BibleError("STALE_BIBLE")
            resolved = sorted(clauses, key=lambda c: c["ref"]) if feature_scope else resolve_clauses(clauses, path)
            return Result("OK", "OK", record={"project_id": project, "bible_pair_digest": bible_pair_digest,
                                                "scope": path, "clauses": resolved},
                          evidence_refs=tuple(c["ref"] for c in resolved))
        except BibleError as exc:
            return _failure(exc)

    def verify_clause_provenance(self, clause_refs: Sequence[str], bible_pair_digest: str) -> Decision:
        try:
            if isinstance(clause_refs, (str, bytes)) or not isinstance(clause_refs, Sequence):
                raise BibleError("INVALID_INPUT")
            refs = tuple(identifier(x) for x in clause_refs)
            if len(refs) != len(set(refs)) or len(refs) > 4096:
                raise BibleError("INVALID_INPUT")
            def check(tx: Any) -> None:
                rows = tx.fetch_all("SELECT DISTINCT project_id FROM v1_bible_editions WHERE active=1", ())
                matches = []
                for row in rows:
                    try:
                        snap, clauses = _current_pair(tx, row["project_id"])
                    except BibleError:
                        continue
                    if snap["pair_digest"] == bible_pair_digest:
                        matches.append({c["ref"] for c in clauses})
                if len(matches) != 1:
                    raise BibleError("STALE_BIBLE")
                if set(refs) - matches[0]:
                    raise BibleError("BIBLE_CONFLICT", tuple(sorted(set(refs) - matches[0])))
            self.state.read(check)
            return Decision(True, "ALLOW", "OK", bible_pair_digest, refs)
        except BibleError as exc:
            return Decision(False, "DENY", exc.code, bible_pair_digest, exc.refs)
