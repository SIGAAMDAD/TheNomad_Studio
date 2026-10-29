"""R9 new-only Bible records and content-addressed immutable source closure.

The source reader is trusted, injected by P2, and returns actual versioned bytes.
No request-supplied `verified` flag or agent-supplied path list is an attestation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
COMMIT = re.compile(r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
KINDS = frozenset(("DESIGN", "TECHNICAL"))
CLAUSE_KINDS = frozenset(("REQUIREMENT", "GUIDELINE", "OPEN_AREA"))


class BibleError(ValueError):
    def __init__(self, code: str, refs: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.refs = refs


from nomad_studio.contracts.runtime_ports_v1 import Result, Decision


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                          allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise BibleError("INVALID_INPUT") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def hexsha(value: Any) -> str:
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise BibleError("INVALID_INPUT")
    return value


def identifier(value: Any) -> str:
    if not isinstance(value, str) or ID.fullmatch(value) is None:
        raise BibleError("INVALID_INPUT")
    return value


def strict(record: Any, required: set[str], optional: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    if (not isinstance(record, Mapping) or any(type(k) is not str for k in record) or
            not required.issubset(record) or set(record).difference(required | set(optional))):
        raise BibleError("INVALID_INPUT")
    canonical(record)
    return dict(record)


def validate_edition(raw: Mapping[str, Any]) -> dict[str, Any]:
    obj = strict(raw, {"project_id", "kind", "edition_id", "epoch", "manifest_digest", "repo",
                       "source_commit", "annex_digests", "clauses"}, {"owner_activation_ref"})
    identifier(obj["project_id"])
    identifier(obj["edition_id"])
    if type(obj["kind"]) is not str or obj["kind"] not in KINDS or type(obj["epoch"]) is not int or obj["epoch"] < 0:
        raise BibleError("INVALID_INPUT")
    hexsha(obj["manifest_digest"])
    if (type(obj["repo"]) is not str or not obj["repo"] or len(obj["repo"]) > 16384 or
            type(obj["source_commit"]) is not str or COMMIT.fullmatch(obj["source_commit"]) is None):
        raise BibleError("INVALID_INPUT")
    annexes = obj["annex_digests"]
    if not isinstance(annexes, list) or len(set(map(str, annexes))) != len(annexes):
        raise BibleError("INVALID_INPUT")
    for h in annexes:
        hexsha(h)
    clauses = obj["clauses"]
    if not isinstance(clauses, list) or not clauses or len(clauses) > 4096:
        raise BibleError("INVALID_INPUT")
    seen = set()
    for clause in clauses:
        c = strict(clause, {"clause_id", "kind", "scope", "text_digest", "precedence", "source_ref"})
        identifier(c["clause_id"])
        if c["clause_id"] in seen or type(c["kind"]) is not str or c["kind"] not in CLAUSE_KINDS:
            raise BibleError("INVALID_INPUT")
        seen.add(c["clause_id"])
        for k in ("scope", "source_ref"):
            if type(c[k]) is not str or not c[k] or len(c[k]) > 16384:
                raise BibleError("INVALID_INPUT")
        hexsha(c["text_digest"])
        if type(c["precedence"]) is not int or c["precedence"] < 0:
            raise BibleError("INVALID_INPUT")
        scope_path(c["scope"], pattern=True)
        source_path(c["source_ref"])
    if "owner_activation_ref" in obj:
        identifier(obj["owner_activation_ref"])
    return obj


def source_path(path: str) -> str:
    if (type(path) is not str or not path or len(path) > 2048 or path.startswith(("/", "\\")) or
            "\\" in path or "\x00" in path or ":" in path or
            any(segment in ("", ".", "..") for segment in path.split("/"))):
        raise BibleError("INVALID_INPUT")
    return path


def scope_path(value: str, *, pattern: bool = False) -> str:
    if pattern and value == "*":
        return value
    if pattern and value.endswith("/**"):
        return source_path(value[:-3]) + "/**"
    return source_path(value)


def applies(pattern: str, scope: str) -> bool:
    return pattern == "*" or pattern == scope or (pattern.endswith("/**") and
        (scope == pattern[:-3] or scope.startswith(pattern[:-2])))


def resolve_clauses(clauses: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    """Specific scope > wildcard scope, then explicit precedence; requirements accumulate.

    All applicable REQUIREMENTs survive: a guideline or OPEN_AREA cannot cancel one.
    Duplicate clause IDs across editions remain qualified by their edition ID.
    """
    scope_path(scope)
    matched = [c for c in clauses if applies(c["scope"], scope)]
    return sorted(matched, key=lambda c: (c["kind"] != "REQUIREMENT",
                 -len(c["scope"].removesuffix("/**")) if c["scope"] != "*" else 0,
                 -c["precedence"], c["edition_id"], c["clause_id"]))


def source_closure(edition: Mapping[str, Any], trusted: Mapping[str, Any]) -> str:
    """Verify every manifest entry against a trusted, commit-pinned source readback.

    Manifest is canonical JSON {version:1,repo,source_commit,files:[{path,sha256,role}]};
    `files` from the trusted reader must be a *complete* byte map, not names alone.
    """
    if (not isinstance(trusted, Mapping) or type(trusted.get("readback_ref")) is not str or
            not trusted["readback_ref"] or trusted.get("repo") != edition["repo"] or
            trusted.get("source_commit") != edition["source_commit"]):
        raise BibleError("EVIDENCE_INVALID")
    raw = trusted.get("manifest_bytes")
    data = trusted.get("files")
    if not isinstance(raw, bytes) or not isinstance(data, Mapping):
        raise BibleError("EVIDENCE_INVALID")
    if hashlib.sha256(raw).hexdigest() != edition["manifest_digest"]:
        raise BibleError("SOURCE_CONFLICT")
    try:
        manifest = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise BibleError("EVIDENCE_INVALID") from exc
    if canonical(manifest) != raw:
        raise BibleError("EVIDENCE_INVALID")
    m = strict(manifest, {"version", "repo", "source_commit", "files"})
    if type(m["version"]) is not int or m["version"] != 1 or m["repo"] != edition["repo"] or m["source_commit"] != edition["source_commit"]:
        raise BibleError("SOURCE_CONFLICT")
    if not isinstance(m["files"], list) or not m["files"]:
        raise BibleError("EVIDENCE_INVALID")
    entries = {}
    path_keys = set()
    annexes = set()
    for item in m["files"]:
        rec = strict(item, {"path", "sha256", "role"})
        path = source_path(rec["path"])
        hexsha(rec["sha256"])
        if (path.casefold() in path_keys or type(rec["role"]) is not str or
                rec["role"] not in ("ROOT", "ANNEX", "CLAUSE")):
            raise BibleError("EVIDENCE_INVALID")
        path_keys.add(path.casefold())
        entries[path] = rec
        if rec["role"] == "ANNEX":
            annexes.add(rec["sha256"])
    if (set(data) != set(entries) or set(edition["annex_digests"]) != annexes or
            sum(e["role"] == "ROOT" for e in entries.values()) != 1):
        raise BibleError("EVIDENCE_INVALID")
    for path, entry in entries.items():
        blob = data[path]
        if type(blob) is not bytes or hashlib.sha256(blob).hexdigest() != entry["sha256"]:
            raise BibleError("SOURCE_CONFLICT")
    extracted_clauses = trusted.get("clause_bytes", {})
    if not isinstance(extracted_clauses, Mapping) or set(extracted_clauses) - {c["clause_id"] for c in edition["clauses"]}:
        raise BibleError("EVIDENCE_INVALID")
    for clause in edition["clauses"]:
        if clause["source_ref"] not in entries:
            raise BibleError("EVIDENCE_INVALID")
        if entries[clause["source_ref"]]["sha256"] == clause["text_digest"]:
            continue
        excerpt = extracted_clauses.get(clause["clause_id"])
        if (type(excerpt) is not bytes or not excerpt or
                excerpt not in data[clause["source_ref"]] or
                hashlib.sha256(excerpt).hexdigest() != clause["text_digest"]):
            raise BibleError("EVIDENCE_INVALID")
    return trusted["readback_ref"]
