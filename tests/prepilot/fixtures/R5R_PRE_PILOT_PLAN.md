# The Nomad Studio R5R - integration and pre-pilot verification plan

**Plan version:** 1.0, frozen before source changes in this work session.
**Owner instructions:** plan fully before implementation; local implementation/integration first; one batched push to `SIGAAMDAD/TheNomad_Studio:implementation/r5r-student-one-wave-20261030`; inspect that push's resulting CI once; do not merge main. Preserve the manually initialized ChatGPT browser-chat workforce and GitHub relay; no paid model API agents, fabricated browser wake-ups, obsolete sessions compatibility, or source authority inferred from assistant prose.

## Evidence and scope

- R5R input: `03_IMPLEMENTATION_COMPLETION_CHECKLIST.md`, `04_GITHUB_RELIABILITY_DESIGN.md`, `05_FAILURE_INJECTION_MATRIX.md`, `06_RECOVERY_AND_DISPATCH_PROTOCOL.md`, `07_DECISION_LOG_AND_AUTHORITY.md` from the attached handoff. Known most recent *historically verified* remote commit at plan creation: `c2f7b4af0bbe8da30f6d492172559b34f98cd8e6` (the prior conversation, not an independent remote read during this plan).
- Local partial mirror: `/mnt/data/studio_r5r_local`; in particular `src/studio/app/{build_recovery,command_delivery,github_webhooks}.py`, `src/studio/integrations/{github,http,rate_governor}.py`, `src/studio/storage/{workforce,builds,shift_checkpoints}.py` and matching `tests`. This is **not** the full authoritative repository. Baseline evidence: targeted source hashes in `prepilot/evidence/source_fingerprint.txt`; test import failures can be mirror/tool invocation defects.
- Full production factory, complete DDL and 72 route catalog are not present in this local tree. They can be assessed only after a complete authoritative checkout; source-level code here can prove **local subsystem** behavior but never make whole-system acceptance green.
- Goal of this change batch: implement local reliability/pre-pilot harness and any verified seam fixes in bounded CI/readback, command publication, webhook wake and memory/backpressure; integrate with matching test contracts; publish one cohesive remote commit if the source/branch and tests can be reconciled safely.

## Actor, authority and transport fingerprint

| Actor | Starts | Environment | State authority | Communications | Negative test |
| --- | --- | --- | --- | --- | --- |
| Human owner | Human | browser | owner approvals / canonical policy | authenticated Studio + GitHub | worker cannot self-approve or force main merge |
| ChatGPT worker | human initializes ordinary browser conversation once | ChatGPT browser response | C persistent worker/shift/lease and verified checkpoints | G GitHub relay | no synthetic API-worker, auto-wake or fake browser final receipt |
| Studio app / recovery pumps | configured host service | Python + APSW | single C writer + approved source state | authenticated provider reads/writes | no unjournaled duplicate write; no active worker inferred from relay activity |
| GitHub provider | external | GitHub APIs/Actions/webhooks | remote immutable commit/ref/readback | authenticated HTTP | timeout unknown until verified; webhook hint never completes work |
| QA / integration authority | authorized independent reviewer / Head | Studio and Git | E evidence and M canonical integration | verified bytes and receipts | reject wrong project, stale revision, omitted QA or owner authority |

## Evidence tiers and immutable acceptance criteria

- **L0 SOURCE_STATIC**: compile/AST, exact imports/contracts, source digest, diff scope, forbidden substitution scan.
- **L1 UNIT_REFERENCE**: actual local modules with deterministic fake C/G provider; strict read/write counts and state assertions. Does not certify APSW or GitHub.
- **L2 INTEGRATED_LOCAL**: real APSW production factory and full schema, 72 HTTP operations, separate processes and simulated immutable provider with durable write log. Requires full repo/toolchains.
- **L3 HOSTED_CI**: Python/APSW, TypeScript, .NET, provenance, Studio control checks all green on same final source SHA. Run once for coherent milestone; inspect failed job only, never busy-poll.
- **L4 LIVE_EXTERNAL**: a real GitHub provider and already-initialized ChatGPT browser conversation; evidence with commits, authenticated actor and two sequential receipts. Cannot be manufactured by unit tests.

A gate passes only if exact command, input, observed output, exit code, artifact SHA and (for L3/L4) commit/run/actor IDs are recorded. A fail or unsupported environment stays NOT_RUN/FAIL/EXTERNAL_BLOCKED. No conditional acceptance based on previously green SHA.

## Source compatibility and integration contracts

**C writer (exclusive):** `WorkforceStorage.command(project_id, operation, request_id, payload_sha256, fn)` is the idempotent transaction boundary. Each G external effect requires stable project-scoped intent before write. `BuildAPSWRepository.prepare/claim_first_dispatch/observe` owns persisted build state. `BuildRecoveryPump._pending` only trusts recorded C idempotency/result hashes and configured source revisions; provider reads never mutate or dispatch Actions. `CommandDeliveryPump.poll_once` only publishes or reconciles C queued/unknown immutable command bytes. `GitHubWebhookHints._process` checks HMAC and scoped repository/ref, persists GUID+payload hash, wakes bounded reconciliation; it is not authorization. `GitHubRateGovernor` scopes cooldown to credential identity. `StudioRuntime.stop` must not close C under a live publisher. Real browser continuation remains a separately observed host property.

**One writer rule:** all new tests/patches share the single C source contract; no shadow persistence, no new schema or API route, no duplicate provider factory or model runtime. External provider mutations are counted by durable effect identity; exact ref CAS and hash readback precede any ambiguous write replay. New tests may inject fake provider responses but must explicitly label test-only fakes.

## Test inventory: every case has both expected acceptance and rejection condition

### A. Bootstrap, API, identity and authorization (A01-A12)

| ID | Injection / condition | Required assertion | Tier |
| --- | --- | --- | --- |
| A01 | Empty fresh database, first authenticated owner | project/grants/Canon bootstrap succeeds | L2 |
| A02 | Missing or invalid bearer and expired identity | 401, no SQL mutation or leaked token | L2 |
| A03 | Valid principal, wrong project | 403 and no cross-tenant read/write | L2 |
| A04 | Role claiming owner or forged owner proof | approval rejected; no effect | L2 |
| A05 | Owner approval wrong digest/project/subject/revision | fails closed; correct approval succeeds | L2 |
| A06 | Invalid, extra, duplicate JSON keys / NaN / invalid UTF-8 | schema validation rejects; no mutation | L2 |
| A07 | Malformed path/query, oversized HTTP body or missing Content-Length | deterministic 4xx; no side effect | L2 |
| A08 | Full 72 frozen operation registration and concrete ports | exact route shape/handler equality, no stubs | L2 |
| A09 | Replay request ID with changed payload/owner | conflict; identical retry same result | L2 |
| A10 | Concurrent owner and unauthorized worker mutations | serialized approved result; no escalation | L2 |
| A11 | Different authorized projects share a GitHub credential | project grants still enforce isolation | L2 |
| A12 | Startup without required provider credentials or real ports | fail closed; no simulated service reported ready | L2 |

### B. Scheduling, leases, workforce and QA (B01-B13)

| ID | Injection / condition | Required assertion | Tier |
| --- | --- | --- | --- |
| B01 | Human registers browser worker and starts initial shift | stable conversation ID / lineage persists | L2 |
| B02 | HELLO missing, forged or from another shift | cannot enter active state | L2 |
| B03 | Two successive commands to same active shift | order and unique receipt IDs; no double execution | L2/L4 |
| B04 | STOP sent mid-shift | receipt/shift transition correct; no fictitious browser termination | L2 |
| B05 | Lease expired, released, superseded or wrong generation | late receipt cannot complete task | L2 |
| B06 | Wrong worker/shift/task/source revision command | transport and C reject | L2 |
| B07 | Concurrent writer claims same assignment | at most one live generation | L2 |
| B08 | Duplicate command ID with different canonical bytes | reject divergent immutable envelope | L1/L2 |
| B09 | Receipt missing remote hash/commit proof | UNKNOWN, no accepted completion | L2 |
| B10 | Checkpoint rollback, altered bytes, wrong owner or lost file | reject, keep previous durable pointer | L1/L2 |
| B11 | Real QA evidence for wrong submission/revision/project | cannot become accepted | L2 |
| B12 | Delete/change already-accepted QA receipt | rejected by persisted policy | L2 |
| B13 | Shift finishes before final GitHub receipt | rejected; verified final receipt permits finish | L2 |

### C. Immutable GitHub delivery and concurrency (C01-C17)

| ID | Injection / condition | Required assertion | Tier |
| --- | --- | --- | --- |
| C01 | Normal create-only GitHub publication | exactly one PUT, readback same SHA/path/hash | L1/L2 |
| C02 | Preflight GET 401/403/5xx or timeout | QUEUED and no write, retry after recovery | L1/L2 |
| C03 | Write succeeds then reply lost | UNKNOWN then exact readback; no second PUT | L2 |
| C04 | Readback remains unavailable across restarts | UNKNOWN retained; no duplicate | L2 |
| C05 | Existing immutable path contains different body | CONFLICT, no overwrite | L1/L2 |
| C06 | Stale branch head between scan and CAS | reject ref update; no force push | L2 |
| C07 | New commits while scan pagination continues | restart scan; never advance incomplete cursor | L1/L2 |
| C08 | Repo tree contains unknown file or truncated response | reject/hold; do not accept partial batch | L1/L2 |
| C09 | Concurrent same-path writers | one content identity accepted, divergent collision rejected | L2 |
| C10 | More projects than batch limit, failed first project | fair rotation; healthy projects progress | L1/L2 |
| C11 | More commands per project than batch limit | bounded work and no starvation | L1/L2 |
| C12 | First project credential unavailable | remaining projects still polled | L1/L2 |
| C13 | Wrong repo/account/source-vs-comms branch | no publication/dispatch across bound ref | L2 |
| C14 | Repeated scan pages at same immutable tip | cache reused, head rechecked each page | L1 |
| C15 | Same mailbox contains old shifts | old messages ignored without losing active work | L1/L2 |
| C16 | Webhook arrives during in-flight poll | wake retained, fresh reconciliation performed | L1 |
| C17 | Stop during blocked network I/O | C remains open until worker exits; restart without duplicate | L1/L2 |

### D. GitHub HTTP, rate control, webhook security (D01-D17)

| ID | Injection / condition | Required assertion | Tier |
| --- | --- | --- | --- |
| D01 | Valid signed push on approved ref | one durable hint and nonblocking wake | L1/L2 |
| D02 | Duplicate GUID with identical bytes across restart | same result, single persisted receipt | L2 |
| D03 | Duplicate GUID with different payload | conflict, no second state change | L1/L2 |
| D04 | Wrong signature/repo/ref/event | reject or ignore; no authorized mutation | L1/L2 |
| D05 | Duplicate JSON keys, NaN, malformed/truncated/oversize body | safe 4xx, no hint | L1 |
| D06 | Valid workflow_run webhook for authorized source | hints only; independent run lookup verifies status | L1/L2 |
| D07 | Missing webhook / receiver down | fallback polling eventually reads exact ref | L2 |
| D08 | Signal raises after durable hint | durable receipt survives; polling recovers | L1/L2 |
| D09 | 401 invalid token vs 403 permissions | classified terminal/blocked, no flood | L1/L2 |
| D10 | 403 secondary and 429 primary throttle | respect retry-after/reset and credential scope | L1/L2 |
| D11 | Ordinary 403 with no limit header | not treated as cooldown | L1 |
| D12 | Same credential used by many projects | one shared governor; no duplicate authentication storm | L1/L2 |
| D13 | Different credentials, one rate limited | other credential progresses | L1 |
| D14 | Retry-After invalid, HTTP date, NaN/overflow | safe bounded handling | L1 |
| D15 | Webhook signature header case/malformed GUID | appropriate 4xx; no C mutation | L1 |
| D16 | Simultaneous webhook hints and stop | no lost wake, no CPU busy spin | L1 |
| D17 | Provider returns 5xx or request connection drops | preserve UNKNOWN and retry gate; never fake success | L2 |

### E. CI, source integration, releases and restart (E01-E17)

| ID | Injection / condition | Required assertion | Tier |
| --- | --- | --- | --- |
| E01 | Prepared build intent before dispatch | one CAS claim, no repeated workflow POST | L2 |
| E02 | Missed workflow event | bounded readback discovers exact run from C | L2 |
| E03 | Known persisted run ID | direct verified read, no listing pagination | L1/L2 |
| E04 | Unknown run / transient 5xx | no fabricated completion; adaptive bounded backoff | L1/L2 |
| E05 | Webhook arrives during outstanding read | clears stale defer, immediate later poll | L1 |
| E06 | Many unresolved builds, one always UNKNOWN | fair project/build rotation, no starvation | L1/L2 |
| E07 | Source SHA mismatches GitHub run SHA | reject; cannot mark passed | L2 |
| E08 | CI canceled, failed, timed out vs previous green | final-head CI gate remains red | L2/L3 |
| E09 | Wrong workflow target, ref or run ID | reject; correct configured target succeeds | L1/L2 |
| E10 | Tampered idempotency result or missing effect intent | reject readback authority | L1/L2 |
| E11 | Process dies before intent DB commit | no remote dispatch, prior checkpoint survives | L2 |
| E12 | Process dies after intent commit before request | recover prepared effect; no duplicate | L2 |
| E13 | Process dies after write before reply | exact readback recovers; one durable provider mutation | L2 |
| E14 | Process dies halfway through recovery | next process resumes from persisted C cursor | L2 |
| E15 | Two competing heads merge/integrate same source | CAS/owner authorization allows only one canonical promotion | L2 |
| E16 | Attempt release with missing QA, Canon proof, exact source or artifacts | reject; with correct evidence release succeeds | L2 |
| E17 | Final source validation and control on exact same HEAD | all jobs green; capture run IDs and commit | L3 |

### F. Scale, performance, resource and observability (F01-F12)

| ID | Injection / condition | Required assertion | Tier |
| --- | --- | --- | --- |
| F01 | 100, 300 and 500 workers in deterministic fake transport | bounded call rate and task fairness | L1/L2 |
| F02 | 10k pending command/CI rows | deterministic pagination and stable memory | L2 |
| F03 | 10k webhook redelivery attempts | dedupe correct, no O(n^2) per delivery | L2 |
| F04 | Provider latency spike/connection hangs | independent projects progress; shutdown bounded | L1/L2 |
| F05 | Disk full, database locked/busy, WAL corruption | no false commit; preserve last consistent journal | L2 |
| F06 | Content blob missing or unreadable after C commit | fail closed, no fabricated checkpoint | L1/L2 |
| F07 | Large identity, extremely long name, path traversal | reject before file or SQL effect | L1/L2 |
| F08 | Clock skew, monotonic vs UTC clock, future Retry-After | bounded delays, lease clock injection | L1/L2 |
| F09 | Memory pressure from indefinite history | capped caches and explicit eviction | L1/L2 |
| F10 | Excessive validation/tool requests | one batched commit and single exact-SHA CI check | PROCESS |
| F11 | Audit/logging output contains tokens | no secret data; stable incident IDs and fields | L1/L2 |
| F12 | Repeated start/stop, cancellation, errors during close | no leaked thread/closed DB race | L1/L2 |

### G. Critical live limitations and topology (G01-G04)

| ID | Injection / condition | Required assertion | Tier |
| --- | --- | --- | --- |
| G01 | Real human initializes browser worker once | authentic existing conversation ID and HELLO | L4 |
| G02 | Two successive GitHub assignments to same initialized conversation | actual response processes both with verified receipts | L4 |
| G03 | ChatGPT response stops before fetching next assignment | status truthful, no inferred auto-wake | L4 |
| G04 | Owner makes approval in trusted authenticated path | exact decision digest; no assistant-generated authority | L4 |

All R5R F-01..F-20 are covered by A/B/C/D/E/G groups. Live G02 = handoff F-20 and is never satisfiable via a simulated worker. Enumerated cases are the bounded test specification; additional failures observed while running the plan are appended as regression IDs before acceptance.

## Implementation execution program - frozen before implementation

1. **Local source inventory and baseline** (no GitHub write): freeze SHA-256 for each changed module; run targeted tests with explicit `PYTHONPATH=src` and capture test log; identify missing full-repo paths. Fail if changed source differs from expected local baseline.
2. **Reliability patch and negative tests**: repair only evidenced bugs in `build_recovery.py`, `command_delivery.py`, `github_webhooks.py`, and corresponding tests. Add failure-injection harness for backoff, fairness, wake concurrency, invalid webhook and no-double-dispatch. No new schema, provider or API route. Local fake-backed tests remain L1.
3. **Local integration**: run changed module syntax checks, targeted unit matrix, all collectible mirror tests, and scripted controlled workload; document missing dependencies separately. Run before/after diff, imports, owner constraints, git blob hash evidence. If APSW/full source absent, mark L2 NOT_RUN.
4. **One consolidated remote milestone** (only if a sound reconciliation path exists): fetch current branch HEAD once, compare every touched file to baseline/expected change, make one multi-file commit and one expected-SHA ref update on `implementation/r5r-student-one-wave-20261030`; verify exact remote SHA once. Do not force/merge main. If remote baseline diverges, leave prepared patch local and report BLOCKED rather than overwriting.
5. **One exact-SHA hosted gate**: inspect the new commit's source and Studio control workflows. If failure, collect failing job once, report CI_FAIL, stage correction locally for a separately authorized next milestone; do not conceal or busy-poll. One push per this request is a hard limit.
6. **Owner-facing verdict**: L0/L1 status separately from L2/L3/L4 and F-20. Whole pilot is NOT_READY unless all required gates are supported by witnessed evidence, even with green source CI.

## Source no-touch boundaries, stop conditions and known gaps

- No changes to normalized C schema, 72 OpenAPI contract, source ownership, production factory outside required integration seam, Canon policy, QA authority, team merge rules, default/main branch or browser-worker topology.
- STOP on unverified baseline or remote history mismatch before pushing; STOP on any regression that causes unauthorized write/unsafe retry; STOP on leaked credentials, cross-project authority, duplicate externally visible effect or wrong SHA. Do not fake pass statuses for unsupported full-repo tests.
- Only a single integration owner modifies these exact source paths in the session; other workers or external CI may test but not authoritatively approve.
- Full-checkout absence is a genuine blocker to L2/complete 72-route acceptance; missing browser wake is a separate L4 blocker. Plan can be used now for local development while those gates remain explicit.

## Three-pass plan audit before source editing

**Fidelity:** owner topology, no forced default merge, local-first and one remote push, no extra wave or API-worker substitution; all explicitly required R5R failure categories mapped. **Contract:** C single writer, project and ref binding, G immutable readback, I receipt, D lease, E QA, M integration and lifecycle; all proposed tests have exact positive/negative expected outcomes. **Adversarial:** includes fake provider success versus actual remote proof, stale/duplicate commands, process kill, cross-project replay, wrong owner approval, stale lease, fork head race, cancellation and same-browser continuation. L2/L3/L4 are explicitly NOT_RUN until executed. This is a self-audit of the plan, not independent QA.
