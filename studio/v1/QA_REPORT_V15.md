# The Nomad Studio V1.5 — independent QA and capacity audit
Audit date: 2026-10-27. Implementation merged in PR #27 at `2c92313ad83369f3c777489f5448c5fd5b25a1fc` (2026-10-27 20:49:17 UTC). Source: `SIGAAMDAD/TheNomad_Studio` `main` baseline `755740cb46b1ea281876c9eeb7ba17385c4e2908`, patch branch `audit/v1-500-worker-qa`.

## Verified CI at patch SHA `c6cd8ee0e1da501611b2e0ad2e4115739ad05613`
| Test gate | Run | Result |
| --- | --- | --- |
| Controller regression suite | 40580745498 | PASS |
| Studio communications tests | 40580745414 | PASS |
| 500-worker synthetic scale tests | 40580745624 | PASS |
| Studio specialist skill contract | 40580745421 | PASS |
| Event-worker routing | 40580745368 | PASS |
| Controller push validation | 40580735474 | PASS |

## Synthetic load evidence
The V1.5 500-worker / 10,000 observation-event **in-memory** projector benchmark ran successfully and completed in 0.003716 seconds on a GitHub Actions runner (run 40579584687). It produced zero redundant peer inbox messages. Five scale/negative tests check 500 registrations, over-cap rejection, broadcast cap, directed handoff and ACK, and unaddressed telemetry ACK rejection. Existing tests cover archive deduplication, wrong role/epoch, orphan ACK, source SHA, QA separation and missing ledger reporting.

This microbenchmark excludes GitHub API/Action queue, git commits/checkout, real conversation invocation, network, sustained multi-hour work, source compiler checks and external queue. It does **not** establish that 500 concurrent live chats work.

## Current live gates
- Actual CW worker registry: **0**.
- `source_writes_enabled`: `false`; `auto_ai_enabled`: `false`; `auto_dispatch_enabled`: `true`.
- GitHub repository rulesets: HTTP 403; private repository subscription restriction. An authenticated head-only merge policy **cannot be enforced by the current available ruleset endpoint**.
- Work W01–W05 prompts were updated but their connected tasks remain disabled. They reference this V1.5 kernel and must not be activated until that source is reviewed and available.
- Oversight synthetic crypto tests pass in separate PR at https://github.com/SIGAAMDAD/TheNomad_Studio_Oversight/pull/3; no real case/notice service was enabled.

## Severity-ranked unresolved architecture gaps
**P0**: Real 100–500 ChatGPT responses cannot be spawned/woken by GitHub commits. Only actual active, scheduled or explicitly externally invoked model executions perform work. Require a verified executor and approved credit/cost policy for unattended runs.

**P0**: Shared owner GitHub credentials cannot attest worker identity/head-only merges. Repository protection unavailable here; keep source writes disabled until an enforceable independently authenticated merge gate is operational.

**P1**: Full Issue comment reconciliation scans at most 1,000 messages/Issue per run and has no durable per-issue cursor. A saturated or long-lived Issue can prevent missed-event recovery. Require V2 WG-B cursor/replay with rate-limit and outage tests.

**P1**: Full archived-event replay (limit 100,000) and single GitHub comms writer serialize/reprocess large traffic. Require V2 WG-C/D durable journal/outbox and incremental projector, then V2 WG-F hash-anchored batches.

**P1**: No real worker-to-worker sustained end-to-end pilot, source CI publisher, S00 accessible schedule, or verified 23:00 America/Chicago Gmail send. Do not declare production acceptance.

**P2**: 190 legacy launcher prompts repeated boilerplate. V1.5 centralizes execution rules, keeps role-specific outputs and acceptance tests, and requires one ledger event per substantive response plus targeted cross-worker messages.

## Acceptance recommendation
CI **PASS** for code-level V1.5 patch; **NOT_RUN** for real 500-chat concurrent execution and all external authorization/deployment gates. PR #27 was subsequently merged through the shared owner account; this is itself evidence that department-head-only source merges are not enforced. Treat the V1.5 patch as merged and the remaining deployment gates as BLOCKED. Only expand live workers after isolated 2–5-worker end-to-end trials, longer soak, actual lease fencing and validated head-only merge denial tests. The existing merged V2 hyperscale architecture is a proposal, not deployed infrastructure.

## Additional discovered production failure
Scheduled `Studio hourly reconciliation` failed on run [40579563334](https://github.com/SIGAAMDAD/TheNomad_Studio/actions/runs/40579563334) because its copied controller scripts omitted `source_targets.py` and `architecture_enforcer.py`. This affected actual hourly runtime processing, not just tests. Targeted dependency-bundle fix: [PR #29](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/29), first controller CI at `354bfec1b986faa000b4d1d89373dbca35088554` PASS. A real successful scheduled reconciliation after merge remains NOT_RUN; do not call hourly dispatch operational until that evidence exists.
