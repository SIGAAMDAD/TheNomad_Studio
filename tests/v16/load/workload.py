"""WG-H seeded workload driver. All reported work uses in-memory fakes only."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import time
import tracemalloc

SYSTEM = Path(__file__).resolve().parents[1] / "system"
if str(SYSTEM) not in sys.path:
    sys.path.insert(0, str(SYSTEM))
from fake_pipeline import FakeJournal, FakeProvider, SimulatedCrash, digest, publish

STEPS = (1, 5, 25, 100, 500)
FAULTS = ("none", "timeout_after_effect", "timeout_before_effect", "crash_after_reservation",
          "crash_after_provider_effect", "drive_partial", "rebase_conflict", "stale_lease",
          "forged_identity", "policy_downgrade")
CONTRACT = Path(__file__).resolve().parents[3] / "studio/v16/contracts/FREEZE_MANIFEST.v1.json"


def percentile_ns(samples, percentage):
    ordered = sorted(samples)
    return ordered[min(len(ordered)-1, max(0, (len(ordered)*percentage + 99)//100-1))] / 1e6 if ordered else 0


def run_profile(actors, seed=1729, faults=True):
    if actors not in STEPS:
        raise ValueError("allowed actor counts: 1, 5, 25, 100, 500")
    rng = random.Random(seed)
    order = list(range(actors))
    rng.shuffle(order)
    plans = [(i, (FAULTS[(i+seed) % len(FAULTS)] if faults else "none")) for i in order]
    workload_sha256 = digest({"seed": seed, "actors": actors, "plans": plans, "faults": faults})
    timings = []
    status = {"lost": 0, "duplicate": 0, "unauthorized": 0, "conflict": 0,
              "recovered": 0, "expected_fail_closed": 0, "verified": 0}
    totals = {"claims": 0, "claim_collisions": 0, "denied": 0, "duplicate_effects_prevented": 0,
              "provider_calls": 0, "provider_readbacks": 0, "provider_effects": 0,
              "outbox_delivered": 0}
    start_wall = time.perf_counter_ns()
    start_cpu = time.process_time_ns()
    tracemalloc.start()
    try:
        for actor, fault in plans:
            started = time.perf_counter_ns()
            db = FakeJournal(worker=f"actor-{actor}")
            provider = FakeProvider()
            worker = f"actor-{actor}"
            artifact = hashlib.sha256(f"{seed}:{actor}".encode()).hexdigest()
            key = f"pub-{actor}"
            if db.claim(worker, f"claim-{actor}") != "COMMITTED":
                status["lost"] += 1
            # A collision and duplicate claim are intentional adversarial load.
            db.claim(worker, f"collision-{actor}")
            db.claim(worker, f"claim-{actor}")
            # Out of order transport, duplicate webhook, ACK replay and durable outbox.
            db.ack(key, worker, 1, "inbox")
            db.ack(key, worker, 1, "inbox")
            db.accept_event(key, {"artifact": artifact})
            db.accept_event(key, {"artifact": artifact})
            db.enqueue(key, "audit", "NOTICE", {"artifact": artifact})
            db.enqueue(key, "audit", "NOTICE", {"artifact": artifact})
            db.deliver(key, "audit", "NOTICE")
            db.deliver(key, "audit", "NOTICE")
            kwargs = {"worker": worker}
            if fault == "stale_lease":
                result = publish(db, provider, key, artifact, worker=worker, generation=0)
            elif fault == "forged_identity":
                result = publish(db, provider, key, artifact, worker=worker, verified=False)
            elif fault == "policy_downgrade":
                result = publish(db, provider, key, artifact, worker=worker, policy_revision="old")
            elif fault == "rebase_conflict":
                provider.head = "concurrent-branch-head"
                result = publish(db, provider, key, artifact, worker=worker)
            else:
                call = {"kind": "drive", "expected": 1} if fault == "drive_partial" else {}
                if fault == "timeout_after_effect":
                    call["fault"] = "timeout_after_effect"
                elif fault == "timeout_before_effect":
                    call["fault"] = "timeout_before_effect"
                elif fault == "drive_partial":
                    call["fault"] = "partial_drive_write"
                elif fault.startswith("crash_after_"):
                    call["crash_at"] = fault.removeprefix("crash_")
                try:
                    result = publish(db, provider, key, artifact, worker=worker, **call)
                except SimulatedCrash:
                    result = "RETRYABLE_BLOCKED"
                if fault == "none":
                    if result != "VERIFIED":
                        status["lost"] += 1
                else:
                    if result != "RETRYABLE_BLOCKED":
                        status["lost"] += 1
                # Simulate DB process crash/restore before the retry; preserve publication.
                if result == "RETRYABLE_BLOCKED":
                    db = FakeJournal.restore(db.snapshot())
                    result = publish(db, provider, key, artifact, worker=worker,
                                     **({"kind": "drive", "expected": 1} if fault == "drive_partial" else {}))
                    if result == "VERIFIED":
                        status["recovered"] += 1
            if fault in ("stale_lease", "forged_identity", "policy_downgrade", "rebase_conflict"):
                expected = {"stale_lease": "STALE_LEASE", "forged_identity": "IDENTITY_UNVERIFIED",
                            "policy_downgrade": "POLICY_REVISION_MISMATCH", "rebase_conflict": "REBASE_REQUIRED"}[fault]
                if result != expected or provider.effects:
                    status["unauthorized"] += 1
                else:
                    status["expected_fail_closed"] += 1
            elif result != "VERIFIED":
                status["lost"] += 1
            else:
                status["verified"] += 1
                # Equal key replay must not cause another external effect.
                if publish(db, provider, key, artifact, worker=worker,
                           **({"kind": "drive", "expected": 1} if fault == "drive_partial" else {})) != "VERIFIED":
                    status["lost"] += 1
                if publish(db, provider, key, "0" * 64, worker=worker) != "IDEMPOTENCY_CONFLICT":
                    status["conflict"] += 1
            if provider.counters.provider_effects > 1:
                status["duplicate"] += provider.counters.provider_effects - 1
            for name in totals:
                source = provider.counters if name.startswith("provider_") else db.stats
                totals[name] += getattr(source, name)
            timings.append(time.perf_counter_ns() - started)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    wall_ns = time.perf_counter_ns() - start_wall
    cpu_ns = time.process_time_ns() - start_cpu
    result = {
        "schema_id": "WG-H/SYNTHETIC-LOAD-REPORT/1", "synthetic_only": True,
        "external_provider_calls": 0, "real_chat_sessions": 0,
        "seed": seed, "actors": actors, "faults_enabled": faults,
        "workload_sha256": workload_sha256,
        "fault_distribution": {name: sum(1 for _, f in plans if f == name) for name in FAULTS},
        "invariants": status, "counters": totals,
        "instrumentation": {
            "wall_ms": round(wall_ns/1e6, 3), "cpu_ms": round(cpu_ns/1e6, 3),
            "p95_actor_ms": round(percentile_ns(timings, 95), 3),
            "p99_actor_ms": round(percentile_ns(timings, 99), 3),
            "peak_traced_python_bytes": peak,
            "synthetic_actors_per_second": round(actors/max(wall_ns/1e9, 1e-9), 2),
            "simulated_provider_request_count": totals["provider_calls"] + totals["provider_readbacks"],
        },
        "abort": any(status[k] for k in ("lost", "duplicate", "unauthorized", "conflict")),
        "not_measured": ["actual PostgreSQL ACID/PITR or migrations", "real identity/credential isolation",
                         "actual GitHub/Drive branch/API requests", "real ChatGPT concurrency",
                         "model tokens or monetary cost", "end-to-end queue and CI latency"],
    }
    return result


def run_profiles(seed=1729, faults=True):
    manifest = json.loads(CONTRACT.read_text())
    return {"report_kind": "WG-H/SYNTHETIC-PROGRESSION/1", "contract_bundle_sha256": manifest["bundle_sha256"],
            "contract_approval_status": manifest["status"], "results": [run_profile(n, seed, faults) for n in STEPS]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--actors", type=int, choices=STEPS, default=None)
    parser.add_argument("--no-faults", action="store_true")
    parser.add_argument("--out", type=Path, help="write JSON report (synthetic only)")
    args = parser.parse_args(argv)
    report = (run_profile(args.actors, args.seed, not args.no_faults)
              if args.actors is not None else run_profiles(args.seed, not args.no_faults))
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized)
    print(serialized, end="")
    results = report["results"] if "results" in report else [report]
    return 1 if any(r["abort"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
