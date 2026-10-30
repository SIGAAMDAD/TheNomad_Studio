import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from studio.agents.contracts import (
    ContractError, canonical_bytes, parse_message, parse_receipt, sha256,
    relay_path, receipt_path,
)

D = "a" * 64


def message(**changes):
    m = dict(protocol_version="1", message_id="m1", project_id="p1", channel_id="c1",
             from_session_id="s1", to_session_id="s1", task_id="t1", lease_generation=1,
             kind="HELLO", issued_at="2026-10-30T06:00:00Z", payload_digest=D,
             payload_ref="sha256:" + D, canon_snapshot_id="canon1", contract_digest=D)
    m.update(changes)
    return m


def receipt(**changes):
    r = dict(protocol_version="1", receipt_id="r1", project_id="p1", channel_id="c1",
             message_id="m1", recipient_session_id="s1", kind="SEEN",
             observed_message_sha256=D, checkpoint_digest=None,
             issued_at="2026-10-30T06:00:00Z", contract_digest=D)
    r.update(changes)
    return r


class ContractTests(unittest.TestCase):
    def test_message_golden(self):
        self.assertEqual(parse_message(canonical_bytes(message())), message())

    def test_receipt_golden(self):
        self.assertEqual(parse_receipt(canonical_bytes(receipt())), receipt())

    def test_self_hello(self):
        with self.assertRaises(ContractError):
            parse_message(canonical_bytes(message(to_session_id="s2")))

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ContractError):
            parse_message(canonical_bytes(message(role="owner")))

    def test_untrusted_owner_fields_rejected(self):
        with self.assertRaises(ContractError):
            parse_receipt(canonical_bytes(receipt(owner_approved=True)))

    def test_wrong_case_receipt_kind(self):
        with self.assertRaises(ContractError):
            parse_receipt(canonical_bytes(receipt(kind="seen")))

    def test_no_nan_or_boolean_generation(self):
        with self.assertRaises(ContractError):
            parse_message(canonical_bytes(message(lease_generation=True)))

    def test_no_duplicate_json_keys(self):
        wire = canonical_bytes(message())
        malicious = wire[:-1] + b',"message_id":"overwrite"}'
        with self.assertRaises(ContractError):
            parse_message(malicious)

    def test_only_canonical_bytes(self):
        with self.assertRaises(ContractError):
            parse_message(json.dumps(message(), indent=2).encode())

    def test_reject_wrong_payload_hash(self):
        with self.assertRaises(ContractError):
            parse_message(canonical_bytes(message(payload_ref="sha256:" + "b" * 64)))

    def test_no_traversal(self):
        for root in ("../oops", "/absolute", "a//b", "a/./b", "a/../b"):
            with self.subTest(root=root), self.assertRaises(ContractError):
                relay_path(root, "s1", "m1")
        with self.assertRaises(ContractError):
            receipt_path("a/b", "../danger", "m1", "r1")

    def test_time_requires_offset(self):
        with self.assertRaises(ContractError):
            parse_message(canonical_bytes(message(issued_at="2026-10-30T06:00:00")))

    def test_bounded_envelope(self):
        with self.assertRaises(ContractError):
            parse_message(b"x" * 65537)


if __name__ == "__main__":
    unittest.main()
