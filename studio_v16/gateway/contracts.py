"""Offline, immutable WG-A contract validation (no network schema resolution)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCHEMAS = Path(__file__).resolve().parents[2] / "studio" / "v16" / "schemas"
SUPPORTED = {
    "AUTH-V16/EVALUATE/1": "auth-evaluate",
    "AUTH-V16/GRANT/1": "auth-grant",
    "AUTH-V16/DECISION/1": "auth-decision",
    "PUBLISH-V16/INTENT/1": "publish-intent",
    "PUBLISH-V16/READBACK/1": "publish-readback",
}


class ContractError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@lru_cache(maxsize=1)
def _validators() -> dict[str, Draft202012Validator]:
    records: list[tuple[str, Resource]] = []
    schemas: dict[str, Any] = {}
    for path in SCHEMAS.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        records.append((schema["$id"], Resource.from_contents(schema)))
        schemas[path.name.removesuffix(".v1.schema.json")] = schema
    registry = Registry().with_resources(records)
    return {kind: Draft202012Validator(schemas[name], registry=registry,
                                       format_checker=FormatChecker())
            for kind, name in SUPPORTED.items()}


def validate(document: object, expected: str) -> dict:
    if not isinstance(document, dict):
        raise ContractError("MALFORMED_REQUEST")
    if document.get("schema_id") != expected:
        raise ContractError("UNKNOWN_SCHEMA_VERSION")
    if not _validators()[expected].is_valid(document):
        raise ContractError("MALFORMED_REQUEST")
    return document


def canonical_hash(value: object) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()
