# The Nomad Studio V1 — Operational Control Plane

[Version and compatibility](studio/VERSION.json) · [Execution protocol](studio/v1/OPERATING_CONTRACT.md) · [Launch checklist](studio/v1/LAUNCH_CHECKLIST.md) · [Worker prompts](studio/v1/prompts/INDEX.md) · [Merge authorization](studio/v1/MERGE_AUTHORITY.md)

**Lead Director → Studio Production Director (S00) → 12 accountable department heads → 174 specialized roles → independent QA → head-authorized merges.** Every substantive worker response commits a department ledger event and mirrors its attempt metadata through the GitHub Issue communication relay. Heads retain parent ownership across executions; junior engineers must checkpoint/yield rather than declaring unfinished work complete.

- `main`: reviewed source and policies. `studio/runtime`: approved jobs, worker registry, ledger, receipts and leases. `studio/comms`: immutable transport events, inbox projections and hourly/23:00 Central reports. The branches are intentionally different histories.
- GitHub Actions is the deterministic broker, not an AI runner. Active/manual chats fetch the inbox when executing. Each logical CW worker is registered explicitly; a GitHub commit does not awaken an inactive chat.
- Source execution W01–W05 targets the authorized *source repository*, not this Studio hub. Source merge requires a separately authenticated department head with exact-SHA QA/CI and Lead Director gates.
- Worker-health auditor cross-checks STARTED/HEARTBEAT/YIELDED/SUBMITTED events against immutable per-response ledger attempts and flags missing checkpoints, overdue progress and unacknowledged handoffs.
- Live runtime gates: source_writes_enabled=false; auto_ai_enabled=false; auto_dispatch_enabled=true (owner-enabled non-spending pilot dispatch). Existing runtime jobs and features are not erased by the V1 product version reset. Current live chat-worker registry is empty.

**Validation:** `python -m unittest discover -s tests -q`, `python scripts/studio.py validate`, `python scripts/production.py validate` and reviewed GitHub Actions checks. These prove code invariants; cross-chat runtime, Gmail delivery, source CI publisher credentials and head merge permissions need their own witnessed acceptance runs.

## Historic documentation

Files with V2/V3/V4 in their names remain as compatibility and prior design records, not authoritative V1 launch instructions. Original 174 specialist manuals are retained. The stale GOAP control-plane draft is addressed in [GOAP cleanup](studio/v1/GOAP_CLEANUP.md); actual GOAP source remains its own repository.
