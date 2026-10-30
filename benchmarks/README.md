# The Nomad Studio R9: performance-only benchmark suite

Runs against the **current R9 application and frozen 56-table database contract**. The suite does not change any production API, table, compatibility behavior, or release acceptance gate. All generated databases, CAS objects and TLS certificates live in disposable temporary directories. External provider receipts are synthetic and explicitly identified.

## Run

```sh
python benchmarks/r9_performance.py --profile smoke --trials 3 --output r9-perf-smoke.json
python benchmarks/r9_performance.py --profile standard --trials 7 --output r9-perf-standard.json
python benchmarks/r9_performance.py --profile scale --trials 7 --output r9-perf-scale.json
```

`standard` is the complete benchmark *coverage* profile. `smoke` executes the same types of benchmarks at smaller sizes; `scale` increases history to 250,000 outbox rows, graph size to 16,384 tasks and contention to 16 threads. At least two trials are required. The seed defaults to `90134`.

`openssl` is required only for the loopback mTLS benchmark; it is reported as skipped if unavailable. All other cases use the standard library. The optional GitHub Actions **Studio R9 performance measurements** workflow runs on request and when benchmark source/workflow files change. It records artifacts without an SLO threshold or production-readiness claim.

## Measurements

| Domain | Workload | Primary metrics |
|---|---|---|
| Composition/startup | Fresh schema and cold construction, restart, readonly inspector | ms per startup, trial p95/p99 |
| SQLite read/write | Scoped reads, durable writes, 1/4/8/16-thread contention | throughput units/s, latency per operation |
| Outbox historical growth | 0/1k/10k/50k/250k ACKED rows | pending poll and health latency vs. database size |
| Outbox backlog | 1k/10k/50k/250k PENDING rows | list and health latency; bytes of RSS growth |
| Outbox publication | Atomic batch append | messages/s and batch commit latency |
| Outbox delivery | End-to-end append/poll/dispatch/ACK; separate individual dispatch calls | messages/s, individual p50/p95/p99 |
| Restart/recovery | 300/3k/10k CLAIMED records | time to recover as UNKNOWN at restart |
| CAS | 4KiB, 64KiB, 1MiB immutable blobs | durable put/read+SHA verification bytes/s |
| CAB-B services | Authenticated-project fixture registration, immutable brief admission | persistent API throughput with synthetic owner receipts |
| CAB-I/CAB-G helpers | Resource path normalization/overlap and 64-capability adapter manifest | calls/s and measured latency |
| WorkGraph | Chain, wide, fan-in DAGs, 128-16,384 tasks | critical-path traversal time and tasks/s |
| Gateway | Loopback mutual-TLS handshake + malformed HTTP request | request round-trip latency including handshake |

Each record includes the workload parameters, raw nanosecond samples, median/mean/p95/p99 (trial based unless otherwise marked), operation counts, throughput, resident-memory snapshots where supported, and Python/SQLite/OS/CPU metadata. **Rows are pre-seeded outside timed sections** for historical-read cases, and CAS bytes/hashes are prepared outside the timed I/O; the combined outbox end-to-end case is explicitly labeled as including append and polling.

The database history rows, TLS certificates, worker-like threads and provider receipts are **local synthetic load only**. A local thread is not a real AI worker. The invalid HTTP request benchmark measures transport and parsing cost; it does not measure authorized owner-command completion. Cold/warm startup measures local Python+SQLite composition, not remote provider activation. RSS is a process snapshot, not per-operation allocation instrumentation; repeated cases may share Python memory arenas and OS page cache. The benchmark does not purport to measure live provider latency, remote network latency, external token spend, or unattended multi-agent capacity.

## Evaluation

No invented latency or throughput budget is hard-coded. A completed benchmark writes machine-readable JSON; an exception indicates a benchmark execution failure rather than a performance regression. Compare identical case/workload records from **the same machine/OS/Python/SQLite setup**, at the same profile/trial count and under comparable load. Differences within sample variance cannot establish a regression. If dedicated hardware or real providers become available, repeat this suite on that environment before setting owner-approved thresholds.
