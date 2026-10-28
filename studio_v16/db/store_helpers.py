"""Shared canonical JSON encoding for persisted immutable authority records."""
import json


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
