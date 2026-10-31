"""Frozen machine transitions, checked before any named business-state update."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .errors import Conflict, Invalid
from .schema import SCHEMA_DIR

STATE_COLUMNS = {"relay_messages": "delivery_state"}


class StateMachines:
    def __init__(self):
        path = SCHEMA_DIR / "state_machines.json"
        raw = path.read_bytes()
        expected = (SCHEMA_DIR / "state_machines.sha256").read_text().split()[0]
        if hashlib.sha256(raw).hexdigest() != expected:
            raise Invalid("frozen state machine digest mismatch")
        self.transitions: Mapping[str, Mapping[str, list[str]]] = json.loads(raw)["transitions"]

    def state_column(self, table: str) -> str:
        return STATE_COLUMNS.get(table, "state")

    def verify(self, table: str, previous: Any, next_state: Any) -> None:
        if table not in self.transitions:
            return
        states = self.transitions[table]
        if previous not in states or next_state not in states:
            raise Invalid("unregistered state name")
        if previous == next_state:
            # Duplicate requests must be replayed through IdempotencyJournal and
            # must not generate another state transition/event.
            raise Conflict("same-state write is not a state transition; replay request result")
        if next_state not in states[previous]:
            raise Conflict(f"illegal {table} state transition")
