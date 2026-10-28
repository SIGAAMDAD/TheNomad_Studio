"""Storage contract and security policy shared across durable backends."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, RefResolver

SCHEMAS = Path(__file__).resolve().parents[2] / 'studio' / 'v16' / 'schemas'


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _digest(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode('utf-8')).hexdigest()


def _validator(name: str) -> Draft202012Validator:
    docs = [json.loads(p.read_text(encoding='utf-8')) for p in SCHEMAS.glob('*.schema.json')]
    registry = {d['$id']: d for d in docs}
    schema = next(x for x in docs if x['$id'] == f'urn:nomad:studio:v16:{name}:1')
    return Draft202012Validator(schema, resolver=RefResolver.from_schema(schema,store=registry), format_checker=FormatChecker())


COMMAND_VALIDATOR = _validator('db-command')
RESULT_VALIDATOR = _validator('db-result')
EVENT_VALIDATOR = _validator('db-event')
RESUME_VALIDATOR = _validator('run-resume')

class StorageError(RuntimeError):
    pass

class Denied(StorageError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

@dataclass(frozen=True)
class GatewayContext:
    """Authenticated principal/verified provider facts. WG-D constructs this server-side.

    `verified_gateway` is a *precondition* asserted by the in-process trusted adapter;
    this class is not a cryptographic authentication service or public RPC DTO.
    """
    principal_id: str
    verified_gateway: bool = False
    provider_readback_verified: bool = False
    verified_external_receipt: bool = False
    verified_source_base: bool = False


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _fresh(prefix: str) -> str:
    return prefix + uuid.uuid4().hex



def deny_if_untrusted(context: GatewayContext):
    if not context.verified_gateway or not context.principal_id:
        raise Denied('IDENTITY_UNVERIFIED')


def make_result(command_id, status, denial=None, seq=None, generation=None, event=None, checkpoint=None):
    result = dict(schema_id='DB-CONTRACT/RESULT/1', command_id=command_id, result=status,
                  denial_code=denial, commit_sequence=seq, lease_generation=generation,
                  journal_event_id=event, checkpoint_sha256=checkpoint, recorded_at=_utc())
    if command_id != 'invalid':
        RESULT_VALIDATOR.validate(result)
    return result
