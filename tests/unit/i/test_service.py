from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from studio.agents import ManualSessionService, AuthorizationError, ContractError, StateError, ExternalEffectUnknown
from studio.agents.contracts import canonical_bytes, parse_message, sha256
from studio.agents.ports import Assignment, CanonCitation, Channel, Checkpoint, RemoteFile, RemotePage

D = "a" * 64
E = "b" * 64
REF_SHA = "c" * 40
ACTOR = {"principal_id": "owner1", "issuer": "auth1", "allowed_project_ids": ["p1"], "trusted_proof_ref": "signed"}


class FakeAuthority:
    def __init__(self):
        self.channel = Channel("p1", "ch1", "o/repo", "comms", "nomad/relays/p1")
        self.assignment = Assignment("p1", "task1", 1, "active", "canon1", D, "pkg1", D,
                                     E, D, "owner1", self.channel, "cab1", "role1",
                                     (CanonCitation("canon1", "fact1", "edition1", "clause1", 3, D, "Canon clause X"),), "High reasoning; manual browser")
        self.allow_relay = True
        self.quiet = None
    def require_project_action(self, actor, project_id, action):
        if actor is not ACTOR or project_id != "p1":
            raise AuthorizationError("not trusted J owner auth")
        return "owner1"
    def resolve_assignment(self, project_id, task_id, generation, package_digest):
        a = self.assignment
        if (project_id, task_id, generation, package_digest) != (a.project_id, a.task_id, a.lease_generation, a.package_digest):
            raise StateError("unrecognized or stale lease")
        return a
    def resolve_resume(self, old):
        return replace(self.assignment, lease_generation=old.lease_generation + 1)
    def allowed_relay(self, sender, recipient, kind):
        return self.allow_relay
    def quiet_seconds(self, project_id):
        return self.quiet


class FakeGateway:
    def __init__(self):
        self.contents = {}
        self.remote = {}
        self.publish_calls = 0
        self.drop_response = False
        self.no_readback = False
        self.page = None
    def stage_content(self, *, project_id, content):
        h = sha256(content)
        self.contents[h] = content
        return h
    def publish_new_file(self, *, project_id, channel_id, path, content_digest, request_id):
        self.publish_calls += 1
        if path in self.remote:
            raise RuntimeError("immutable path exists")
        body = self.contents[content_digest]
        self.remote[path] = RemoteFile(project_id, channel_id, path, "o/repo", "comms", REF_SHA,
                                       body, sha256(body), True)
        if self.drop_response:
            raise TimeoutError("network returned ambiguous timeout after commit")
    def readback_exact(self, *, project_id, channel_id, path):
        if self.no_readback:
            return None
        return self.remote.get(path)
    def scan_since(self, *, project_id, channel_id, verified_ref_sha, limit):
        return self.page or RemotePage((), REF_SHA, True)
    def scan_next(self, *, project_id, channel_id, page_token, limit):
        return self.next_page
    def remote_file(self, path, body, **changes):
        return RemoteFile("p1", "ch1", path, "o/repo", "comms", REF_SHA,
                          body, sha256(body), True, **changes)


class FakeStore:
    def __init__(self):
        self.sessions = {}
        self.by_request = {}
        self.messages = {}
        self.receipts = {}
        self.checkpoints = {}
        self.lineages = {}
        self.cursor = {}
        self.effects = {}
        self.observations = set()
    def create_session(self, session, request_id, request_hash):
        key = session.project_id, "I", request_id
        if key in self.by_request:
            old_hash, old_id = self.by_request[key]
            if old_hash != request_hash:
                raise StateError("idempotency conflict")
            return self.sessions[old_id]
        self.by_request[key] = request_hash, session.session_id
        self.sessions[session.session_id] = session
        return session
    def get_session(self, project_id, session_id):
        s = self.sessions.get(session_id)
        return s if s and s.project_id == project_id else None
    def transition(self, project_id, session_id, expected, new_state):
        s = self.get_session(project_id, session_id)
        if s is None or s.state not in expected:
            raise StateError("CAS mismatch")
        s = replace(s, state=new_state)
        self.sessions[session_id] = s
        return s
    def list_sessions(self, project_id):
        return [s for s in self.sessions.values() if s.project_id == project_id]
    def get_checkpoint(self, project_id, checkpoint_id):
        v = self.checkpoints.get(checkpoint_id)
        return v if v and v.project_id == project_id else None
    def record_checkpoint(self, checkpoint):
        old = self.checkpoints.get(checkpoint.checkpoint_id)
        if old is not None and old != checkpoint:
            raise StateError("checkpoint immutable")
        self.checkpoints[checkpoint.checkpoint_id] = checkpoint
    def stage_message(self, message, request_id, request_hash):
        old = self.messages.get(message.message_id)
        if old and old.envelope_digest != message.envelope_digest:
            raise StateError("message bytes mismatch")
        if old:
            return old
        self.messages[message.message_id] = message
        return message
    def get_message(self, project_id, message_id):
        m = self.messages.get(message_id)
        return m if m and m.project_id == project_id else None
    def record_observation(self, message, file):
        self.observations.add((message.message_id, file.commit_sha, file.body_sha256))
        old = self.messages[message.message_id]
        self.messages[message.message_id] = replace(old, state="observed", remote_commit_sha=file.commit_sha)
        if old.kind == "HELLO":
            s = self.sessions[old.to_session_id]
            self.sessions[s.session_id] = replace(s, last_observed_at="2026-10-30T06:00:00Z")
    def record_receipt(self, receipt, file):
        old = self.receipts.get(receipt.receipt_id)
        if old is not None and old != receipt:
            raise StateError("receipt immutable")
        self.receipts[receipt.receipt_id] = receipt
    def get_receipts(self, project_id, message_id):
        return [r for r in self.receipts.values() if r.project_id == project_id and r.message_id == message_id]
    def list_unresolved(self, project_id, channel_id):
        return [m for m in self.messages.values() if m.project_id == project_id and m.channel_id == channel_id and m.state != "observed"]
    def get_cursor(self, project_id, channel_id):
        return self.cursor.get((project_id, channel_id))
    def commit_cursor(self, project_id, channel_id, ref_sha):
        self.cursor[project_id, channel_id] = ref_sha
    def record_lineage(self, child, parent):
        self.lineages[child.session_id] = parent.session_id
    def record_effect_intent(self, project_id, message, request_id):
        self.effects[message.message_id] = request_id


def raw_hello(s, mid="hello1", **changes):
    payload = D
    wire = dict(protocol_version="1", message_id=mid, project_id=s.project_id,
                channel_id=s.channel_id, from_session_id=s.session_id, to_session_id=s.session_id,
                task_id=s.task_id, lease_generation=s.lease_generation, kind="HELLO",
                issued_at="2026-10-30T06:00:00Z", payload_digest=payload,
                payload_ref="sha256:" + payload, canon_snapshot_id=s.snapshot_id,
                contract_digest=s.contract_digest)
    wire.update(changes)
    return canonical_bytes(wire)


class SessionServiceTests(unittest.TestCase):
    def setUp(self):
        self.auth = FakeAuthority()
        self.store = FakeStore()
        self.gateway = FakeGateway()
        self.svc = ManualSessionService(authority=self.auth, store=self.store, gateway=self.gateway)

    def start(self, request_id="start1"):
        return self.svc.prepare_launch(project_id="p1", task_id="task1", lease_generation=1,
                                       package_digest="pkg1", request_id=request_id, actor=ACTOR)

    def enroll(self, request_id="start1", mid="hello1"):
        p = self.start(request_id)
        s = self.store.sessions[p["session_id"]]
        body = raw_hello(s, mid)
        path = f"nomad/relays/p1/mail/{s.session_id}/{mid}.json"
        file = self.gateway.remote_file(path, body)
        self.gateway.remote[path] = file
        self.svc.ingest_message(file)
        return p, file

    def test_manual_start_is_prepare_only(self):
        p = self.start()
        self.assertEqual(self.store.sessions[p["session_id"]].state, "awaiting_manual_start")
        self.assertIn("manually open", p["prompt"])
        self.assertIn("role_manifest_digest", p["prompt"])
        self.assertEqual(self.gateway.publish_calls, 0)
        self.assertEqual(p["prompt_digest"], sha256(p["prompt"].encode()))

    def test_start_openapi_shape(self):
        result = self.svc.startWorker(ACTOR, "p1", dict(request_id="start1", task_id="task1",
                              lease_generation=1, package_digest="pkg1"))
        self.assertEqual(set(result), {"session_id", "state"})
        self.assertEqual(result["state"], "awaiting_manual_start")

    def test_start_idempotent_same_result(self):
        self.assertEqual(self.start()["session_id"], self.start()["session_id"])
        self.assertEqual(len(self.store.sessions), 1)

    def test_start_replay_mutated_body_is_rejected(self):
        self.start()
        with self.assertRaises(StateError):
            self.svc.prepare_launch(project_id="p1", task_id="task1", lease_generation=1,
                                    package_digest="other", request_id="start1", actor=ACTOR)

    def test_no_owner_self_assertion(self):
        actor = dict(ACTOR)
        with self.assertRaises(AuthorizationError):
            self.svc.startWorker(actor, "p1", dict(request_id="s", task_id="task1", lease_generation=1, package_digest="pkg1"))

    def test_stale_lease_start_fails(self):
        with self.assertRaises(StateError):
            self.svc.prepare_launch(project_id="p1", task_id="task1", lease_generation=999,
                                    package_digest="pkg1", request_id="start1", actor=ACTOR)

    def test_note_cannot_activate_worker(self):
        p = self.start()
        sid = p["session_id"]
        s = self.store.sessions[sid]
        note = raw_hello(s, "note1", kind="NOTE")
        f = self.gateway.remote_file(f"nomad/relays/p1/mail/{sid}/note1.json", note)
        with self.assertRaises(StateError):
            self.svc.ingest_message(f)
        self.assertEqual(self.store.sessions[sid].state, "awaiting_manual_start")

    def test_verified_self_hello_required(self):
        p = self.start()
        s = self.store.sessions[p["session_id"]]
        bad = raw_hello(s, "h1", to_session_id="other")
        f = self.gateway.remote_file(f"nomad/relays/p1/mail/{s.session_id}/h1.json", bad)
        with self.assertRaises(ContractError):
            self.svc.ingest_message(f)
        self.assertEqual(self.store.sessions[s.session_id].state, "awaiting_manual_start")

    def test_verified_hello_positive_and_idempotent(self):
        p, f = self.enroll()
        self.assertEqual(self.store.sessions[p["session_id"]].state, "active_observed")
        self.svc.ingest_message(f)
        self.assertEqual(self.store.sessions[p["session_id"]].state, "active_observed")
        self.assertEqual(len(self.store.observations), 1)

    def test_wrong_repo_or_ref_hash_rejected(self):
        p = self.start()
        s = self.store.sessions[p["session_id"]]
        f = self.gateway.remote_file(f"nomad/relays/p1/mail/{s.session_id}/hello1.json", raw_hello(s))
        self.assertRaises(StateError, self.svc.ingest_message, replace(f, repo="attacker/repo"))
        self.assertRaises(StateError, self.svc.ingest_message, replace(f, body_sha256=E))
        self.assertRaises(StateError, self.svc.ingest_message, replace(f, commit_sha="0" * 39))

    def test_wrong_lease_and_canon_hello_rejected(self):
        p = self.start()
        s = self.store.sessions[p["session_id"]]
        for changes in ({"lease_generation": 2}, {"canon_snapshot_id": "other"}, {"contract_digest": E}):
            f = self.gateway.remote_file(f"nomad/relays/p1/mail/{s.session_id}/hello1.json", raw_hello(s, **changes))
            with self.subTest(changes=changes), self.assertRaises(StateError):
                self.svc.ingest_message(f)

    def test_remote_send_is_verified_and_deduplicated(self):
        p, _ = self.enroll()
        result = self.svc.sendRelay(ACTOR, "p1", dict(request_id="send1", from_session_id=p["session_id"],
                                       to_session_id=p["session_id"], payload_digest=D))
        self.assertEqual(result["state"], "observed")
        self.assertEqual(self.gateway.publish_calls, 1)
        self.assertIn(result["message_id"], self.store.effects)
        repeated = self.svc.sendRelay(ACTOR, "p1", dict(request_id="send1", from_session_id=p["session_id"],
                                        to_session_id=p["session_id"], payload_digest=D))
        self.assertEqual(repeated, result)
        self.assertEqual(self.gateway.publish_calls, 1)

    def test_network_timeout_then_verified_readback(self):
        p, _ = self.enroll()
        self.gateway.drop_response = True
        r = self.svc.sendRelay(ACTOR, "p1", dict(request_id="send2", from_session_id=p["session_id"],
                                to_session_id=p["session_id"], payload_digest=D))
        self.assertEqual(r["state"], "observed")

    def test_unknown_write_does_not_double_publish(self):
        p, _ = self.enroll()
        self.gateway.no_readback = True
        args = dict(request_id="send3", from_session_id=p["session_id"],
                    to_session_id=p["session_id"], payload_digest=D)
        with self.assertRaises(ExternalEffectUnknown):
            self.svc.sendRelay(ACTOR, "p1", args)
        self.assertEqual(self.gateway.publish_calls, 1)
        self.gateway.no_readback = False
        r = self.svc.sendRelay(ACTOR, "p1", args)
        self.assertEqual(r["state"], "observed")
        self.assertEqual(self.gateway.publish_calls, 1)

    def test_send_before_verified_hello_fails(self):
        p = self.start()
        with self.assertRaises(StateError):
            self.svc.sendRelay(ACTOR, "p1", dict(request_id="send0", from_session_id=p["session_id"],
                                  to_session_id=p["session_id"], payload_digest=D))

    def test_policy_rejects_cross_task_relay(self):
        p, _ = self.enroll()
        self.auth.allow_relay = False
        with self.assertRaises(AuthorizationError):
            self.svc.sendRelay(ACTOR, "p1", dict(request_id="blocked", from_session_id=p["session_id"],
                                  to_session_id=p["session_id"], payload_digest=D))

    def test_stop_advisory_only(self):
        p, _ = self.enroll()
        res = self.svc.cancelWorker(ACTOR, "p1", p["session_id"],
                                    dict(request_id="stop1", reason="owner ended assignment"))
        self.assertEqual(res["state"], "stop_requested")
        self.assertEqual(self.store.sessions[p["session_id"]].state, "stop_requested")
        self.assertEqual(len(self.store.receipts), 0)
        self.assertEqual(self.gateway.publish_calls, 1)

    def test_no_stop_claim_if_remote_unverified(self):
        p, _ = self.enroll()
        self.gateway.no_readback = True
        with self.assertRaises(ExternalEffectUnknown):
            self.svc.cancelWorker(ACTOR, "p1", p["session_id"], dict(request_id="stop2", reason="X"))
        self.assertEqual(self.store.sessions[p["session_id"]].state, "active_observed")

    def test_ack_requires_remote_verified_receipt(self):
        p, _ = self.enroll()
        sid = p["session_id"]
        r = self.svc.sendRelay(ACTOR, "p1", dict(request_id="send4", from_session_id=sid, to_session_id=sid, payload_digest=D))
        self.assertFalse(self.svc.ackRelay(ACTOR, "p1", r["message_id"], dict(request_id="a1", session_id=sid))["acknowledged"])
        msg = self.store.messages[r["message_id"]]
        raw = canonical_bytes(dict(protocol_version="1", receipt_id="receipt1", project_id="p1", channel_id="ch1",
                     message_id=msg.message_id, recipient_session_id=sid, kind="SEEN",
                     observed_message_sha256=msg.envelope_digest, checkpoint_digest=None,
                     issued_at="2026-10-30T06:00:00Z", contract_digest=msg.contract_digest))
        path = f"nomad/relays/p1/receipts/{sid}/{msg.message_id}/receipt1.json"
        self.svc.ingest_receipt(self.gateway.remote_file(path, raw))
        self.assertTrue(self.svc.ackRelay(ACTOR, "p1", msg.message_id, dict(request_id="a1", session_id=sid))["acknowledged"])

    def test_receipt_mismatched_original_and_channel(self):
        p, _ = self.enroll()
        sid = p["session_id"]
        m = self.store.messages["hello1"]
        for digest_value, channel in ((E, "ch1"), (m.envelope_digest, "other")):
            raw = canonical_bytes(dict(protocol_version="1", receipt_id="rbad", project_id="p1", channel_id=channel,
                     message_id=m.message_id, recipient_session_id=sid, kind="SEEN", observed_message_sha256=digest_value,
                     checkpoint_digest=None, issued_at="2026-10-30T06:00:00Z", contract_digest=D))
            f = self.gateway.remote_file(f"nomad/relays/p1/receipts/{sid}/hello1/rbad.json", raw)
            with self.assertRaises(StateError): self.svc.ingest_receipt(f)

    def test_receipt_checkpoint_creates_resumable_context(self):
        p, _ = self.enroll()
        sid = p["session_id"]
        m = self.store.messages["hello1"]
        raw = canonical_bytes(dict(protocol_version="1", receipt_id="checkpoint1", project_id="p1", channel_id="ch1",
                    message_id="hello1", recipient_session_id=sid, kind="CHECKPOINT", observed_message_sha256=m.envelope_digest,
                    checkpoint_digest=E, issued_at="2026-10-30T06:00:00Z", contract_digest=D))
        f = self.gateway.remote_file(f"nomad/relays/p1/receipts/{sid}/hello1/checkpoint1.json", raw)
        self.svc.ingest_receipt(f)
        new = self.svc.prepare_resume(project_id="p1", session_id=sid, checkpoint_id="checkpoint1",
                                      request_id="resume1", actor=ACTOR)
        self.assertNotEqual(new["session_id"], sid)
        self.assertEqual(new["lease_generation"], 2)
        self.assertIn("resume_cursor", new["prompt"])
        self.assertEqual(self.store.lineages[new["session_id"]], sid)

    def test_resume_requires_verified_checkpoint(self):
        p, _ = self.enroll()
        with self.assertRaises(StateError):
            self.svc.prepare_resume(project_id="p1", session_id=p["session_id"], checkpoint_id="madeup",
                                    request_id="resume2", actor=ACTOR)

    def test_quiet_is_not_completed(self):
        p, _ = self.enroll()
        self.auth.quiet = 10
        now = datetime(2026, 10, 31, tzinfo=timezone.utc)
        self.assertEqual(self.svc.mark_quiet(project_id="p1", now=now), 1)
        self.assertEqual(self.store.sessions[p["session_id"]].state, "quiet")

    def test_partial_scan_does_not_advance_cursor(self):
        self.gateway.page = RemotePage((), REF_SHA, False)
        result = self.svc.reconcile_channel(project_id="p1", channel_id="ch1")
        self.assertFalse(result["complete"])
        self.assertIsNone(self.store.get_cursor("p1", "ch1"))
        self.gateway.page = RemotePage((), REF_SHA, True)
        result = self.svc.reconcile_channel(project_id="p1", channel_id="ch1")
        self.assertTrue(result["complete"])
        self.assertEqual(self.store.get_cursor("p1", "ch1"), REF_SHA)

    def test_wrong_snapshot_citation_rejected(self):
        bad = replace(self.auth.assignment, cited_context=(CanonCitation("other", "fact1", "edition1", "clause1", 3, D, "False"),))
        self.auth.assignment = bad
        with self.assertRaises(StateError):
            self.start()

    def test_ref_history_rewrite_freezes_sessions(self):
        p, _ = self.enroll()
        self.gateway.page = RemotePage((), REF_SHA, False, ancestry_verified=False)
        with self.assertRaises(StateError):
            self.svc.reconcile_channel(project_id="p1", channel_id="ch1")
        self.assertEqual(self.store.sessions[p["session_id"]].state, "unknown")
        self.assertIsNone(self.store.get_cursor("p1", "ch1"))

    def test_paginated_scan_exhausts_before_cursor_commit(self):
        self.gateway.page = RemotePage((), REF_SHA, False, next_page_token="page2")
        self.gateway.next_page = RemotePage((), REF_SHA, True)
        result = self.svc.reconcile_channel(project_id="p1", channel_id="ch1")
        self.assertEqual(result["complete"], 1)
        self.assertEqual(self.store.get_cursor("p1", "ch1"), REF_SHA)

    def test_expired_lease_fenced_unknown(self):
        p, _ = self.enroll()
        self.auth.assignment = replace(self.auth.assignment, lease_status="expired")
        self.assertEqual(self.svc.fence_expired_sessions(project_id="p1"), 1)
        self.assertEqual(self.store.sessions[p["session_id"]].state, "unknown")

    def test_stop_seen_cannot_ack_note(self):
        p, _ = self.enroll()
        sid = p["session_id"]
        m = self.store.messages["hello1"]
        raw = canonical_bytes(dict(protocol_version="1", receipt_id="badstop", project_id="p1", channel_id="ch1",
                        message_id="hello1", recipient_session_id=sid, kind="STOP_SEEN",
                        observed_message_sha256=m.envelope_digest, checkpoint_digest=None,
                        issued_at="2026-10-30T06:00:00Z", contract_digest=D))
        f = self.gateway.remote_file(f"nomad/relays/p1/receipts/{sid}/hello1/badstop.json", raw)
        with self.assertRaises(StateError): self.svc.ingest_receipt(f)

    def test_list_workers_openapi_exact_projection(self):
        p = self.start()
        out = self.svc.listWorkers(ACTOR, "p1")
        self.assertEqual(out, {"sessions": [{"session_id": p["session_id"], "task_id": "task1", "state": "awaiting_manual_start"}]})


if __name__ == "__main__":
    unittest.main()
