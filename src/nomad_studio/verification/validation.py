"""R9 frozen quality-record validators. Validation is closed; unknown keys fail.

These are the quality-facing projections of CONTRACTS/records.schema.json. P2
owns schema installation; CAB-E cannot edit or relax the central contracts.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

SHA256 = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}(?:[a-f0-9]{24})?\Z")
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")

TEST_KEYS = frozenset(("test_id", "commit", "exit_code", "log_digest", "trusted_runner_ref",
                       "observed_at_us", "adapter_digest", "toolchain_digest", "input_digest"))
BIBLE_KEYS = frozenset(("project_id", "feature_id", "bible_pair_digest", "subject_digest",
                        "clause_refs_checked", "violations", "semantic_review_ref",
                        "mechanical_check_refs", "result"))
ARTIFACT_KEYS = frozenset(("artifact_id", "project_id", "feature_id", "task_id", "kind",
                           "resource_path", "content_sha256", "producer_principal",
                           "bible_pair_digest", "feature_contract_digest", "byte_proof_ref",
                           "source_parent_commit"))
ARTIFACT_KINDS = frozenset(("SOURCE", "ASSET", "SCENE", "QUEST_GRAPH", "DIALOGUE", "AUDIO", "TEST_LOG", "OTHER"))


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(value: Any, *, commit: bool = False) -> str:
    if not isinstance(value, str) or not (COMMIT if commit else SHA256).fullmatch(value):
        raise ValueError("invalid commit/digest")
    return value


def ref(value: Any) -> str:
    if not isinstance(value, str) or not REF.fullmatch(value):
        raise ValueError("invalid evidence identifier")
    return value


def closed(record: Any, required: frozenset[str], allowed: frozenset[str] | None = None) -> Mapping[str, Any]:
    if not isinstance(record, Mapping) or not required <= record.keys() or not record.keys() <= (allowed or required):
        raise ValueError("record field mismatch")
    if any(not isinstance(key, str) for key in record):
        raise ValueError("non-string field")
    canonical(record)
    return record


def unique_refs(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) > 4096:
        raise ValueError("invalid reference list")
    result = tuple(ref(x) for x in values)
    if len(result) != len(set(result)):
        raise ValueError("duplicate reference")
    return result


def test_evidence(value: Any) -> dict[str, Any]:
    record = closed(value, TEST_KEYS)
    ref(record["test_id"])
    sha(record["commit"], commit=True)
    for key in ("log_digest", "adapter_digest", "toolchain_digest", "input_digest"):
        sha(record[key])
    ref(record["trusted_runner_ref"])
    if type(record["exit_code"]) is not int or record["exit_code"] < 0:
        raise ValueError("invalid test exit")
    if type(record["observed_at_us"]) is not int or record["observed_at_us"] < 1:
        raise ValueError("invalid test timestamp")
    return dict(record)


def bible_receipt(value: Any) -> dict[str, Any]:
    record = closed(value, BIBLE_KEYS)
    for key in ("project_id", "feature_id", "semantic_review_ref"):
        ref(record[key])
    for key in ("bible_pair_digest", "subject_digest"):
        sha(record[key])
    for key in ("clause_refs_checked", "violations", "mechanical_check_refs"):
        unique_refs(record[key])
    if record["result"] not in ("PASS", "FAIL", "NOT_VERIFIED"):
        raise ValueError("unknown bible result")
    return dict(record)


def artifact_manifest(value: Any) -> dict[str, Any]:
    record = closed(value, ARTIFACT_KEYS - {"source_parent_commit"}, ARTIFACT_KEYS)
    for key in ("artifact_id", "project_id", "feature_id", "task_id", "producer_principal", "byte_proof_ref"):
        ref(record[key])
    for key in ("content_sha256", "bible_pair_digest", "feature_contract_digest"):
        sha(record[key])
    if record["kind"] not in ARTIFACT_KINDS:
        raise ValueError("unknown artifact kind")
    if (not isinstance(record["resource_path"], str) or not record["resource_path"] or
        len(record["resource_path"]) > 16384):
        raise ValueError("invalid artifact path")
    if record["kind"] in ("SOURCE", "SCENE"):
        sha(record.get("source_parent_commit"), commit=True)
    elif "source_parent_commit" in record:
        sha(record["source_parent_commit"], commit=True)
    return dict(record)
