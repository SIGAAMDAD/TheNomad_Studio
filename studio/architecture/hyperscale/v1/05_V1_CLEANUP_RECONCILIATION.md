# Studio 1.0.0 cleanup reconciliation — verified October 27, 2026
Status: source-readback compatibility addendum, DESIGN ONLY.

## Observed GitHub changes
- The concurrently running cleanup effort produced and merged PR #24, "Studio 1.0.0: consolidate role hierarchy, relay lifecycle, worker health and launch prompts". Main merge SHA: **49c3a5bba445f1dd927b930283ffdeb3bbbb8b52**. This establishes a Git merge, NOT that the other chat is stopped or still working.
- studio/VERSION.json now defines product version **1.0.0** while retaining runtime schema 1, comms schema 1, department ledger 4 and department registry 4. autonomous_execution_verified=false.
- scripts/studio_comms.py adds **STARTED, HEARTBEAT, YIELDED, SUBMITTED, STOPPED** event types and attempt_id / assignment_epoch / ledger_event_id / elapsed_seconds / resume_required semantics.
- scripts/studio_worker_health.py adds evidence-linked lifecycle/ledger audits and overdue/missing-progress signals.
- tests/test_studio_v1_end_to_end.py builds **100 synthetic worker identities** and verifies a sender-to-recipient handoff, ACK, SUBMITTED and ledger continuity. This is NOT a real 100-concurrent-executor stress test.
- scripts/studio_inbox.py still fully reads the event archive before each projection and carries MAX_EVENTS=100000 and MAX_INBOX_MESSAGES=5000. The serialized GitHub Actions comms writer still remains a throughput bottleneck.
- Current studio/runtime config still reads source_writes_enabled=false, auto_ai_enabled=false, max_concurrent_writers=2, max_reviewers=1 and **auto_dispatch_enabled=true**. The ordinary chat worker registry is empty.
- studio/v1/OPERATING_CONTRACT.md introduces **accountable department-head merging** subject to independently authenticated GitHub credentials, independent exact-SHA QA and owner-approved policy. The human Lead Director owns architecture/canon/budget and the policy governing merges; do not invent a blanket mechanical owner-only click requirement or permit a head to merge with an unverified shared token.
- New individual role prompts and lifecycle instructions are in studio/v1/prompts and studio/v1/OPERATING_CONTRACT.md. Reuse them instead of generating a competing role hierarchy.

## Mandatory reconciliation for the V2 implementation workgroups
1. Treat 49c3a5... or newer **actual current main SHA** as the source implementation baseline. The older aa684e... commit in the design was pinned for provenance only. Read exact refs before coding.
2. Preserve V1.0.0 events, their worker session attempt lifecycle, and ledger correlation. Any V2 event conversion must explicitly version its adapter, retain origin and record field-level transformations.
3. Preserve and run the 100-worker synthetic fixture plus worker-health regressions. Extend them to real durable-queue burst, replay, retention and long-soak validation; do not mislabel existing fixture as that proof.
4. Preserve the 12 department owners, 174 specialist roles and V1 prompt/skill entrypoints.
5. Do not mutate existing comms scripts, Studio controller workflows or source-workstream repositories until the designated final integration group, fresh cleanup diff review and owner gate.
6. Respect the actual Studio 1.0.0 authenticated department-head merge policy and independent QA. Never infer authorization from an agent's asserted role or the shared GitHub owner identity.
7. Keep source writes, auto-AI, reviewer/writer capacities, model spending and private Oversight access unchanged without owner approval.
8. A GitHub merge or absence of an open PR does not reveal the current execution status of the other ChatGPT conversation.

## Source references
- https://github.com/SIGAAMDAD/TheNomad_Studio/pull/24
- https://github.com/SIGAAMDAD/TheNomad_Studio/blob/main/studio/VERSION.json
- https://github.com/SIGAAMDAD/TheNomad_Studio/blob/main/studio/v1/OPERATING_CONTRACT.md
- https://github.com/SIGAAMDAD/TheNomad_Studio/blob/main/tests/test_studio_v1_end_to_end.py
- https://github.com/SIGAAMDAD/TheNomad_Studio/blob/main/scripts/studio_worker_health.py

This addendum does not deploy PostgreSQL, SQS, a new API gateway, ChatGPT workers, source-write policy changes or model automations.
