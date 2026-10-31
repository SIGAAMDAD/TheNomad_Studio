# R5R pre-pilot coverage, result reconciliation and plan audit

The 92-case plan was frozen before any test/code modifications. This report tracks the implementation subset, unresolved cases and crosswalk.

## Exact handoff failure crosswalk

| R5R case | Plan scenario(s) | Required gate |
| --- | --- | --- |
| F-01 | E11 | L2 process kill before DB commit |
| F-02 | E12 | L2 restart between intent commit and remote mutation |
| F-03 | C03, E13 | L2 durable provider history; exactly one external write |
| F-04 | D09 | L1/L2 401 classification |
| F-05 | A03, D09 | L1/L2 403 permissions |
| F-06 | D10, D12, F08 | L1/L2 throttling and credential-wide cooldown |
| F-07 | C02, C04, D17 | L2 post-dispatch timeout ambiguity |
| F-08 | D02, D03 | L2 duplicate webhook across process restart |
| F-09 | D07, E02 | L2 missing webhook triggers readback |
| F-10 | C06, E15 | L2 CAS ref race and no forced overwrite |
| F-11 | E08 | L2 canceled run never green |
| F-12 | E08, E17 | L3 exact final SHA |
| F-13 | B05, B06 | L2 stale lease receipt rejects |
| F-14 | E13, E14 | L2 committed effect recovered after host interruption |
| F-15 | C05, C08 | L2 wrong remote digest cannot publish |
| F-16 | B07, C09, C11 | L2 concurrent writer and mailbox serialization |
| F-17 | E14 | L2 kill recovery midflight |
| F-18 | D07, D08 | L2 failed webhook fallback sweep |
| F-19 | C03, C04, E13 | L4 host continuation absence with exact readback |
| F-20 | G01, G02, G03 | L4 two commands in one real initialized browser conversation |

## Status definitions

- PASS_REFERENCE_ONLY: targeted deterministic local fake-backed test passed. No claim about production writer, ChatGPT, GitHub or complete end-to-end workflow.
- NOT_RUN: no test attempted for that tier; absence of a failure is not success.
- EXTERNAL_BLOCKED: source-side suite cannot exercise browser continuation.
- CI_PASS: exact SHA with checked workflow/job receipts only.
- NOT_READY_FOR_PILOT: mandatory L2/L3/L4 requirements remain.

## Three audit passes after freezing the plan

1. Fidelity (self-audit): owner topology, single wave/branch, one push maximum, source authority and no paid API/model-worker substitute. Result: plan consistent.
2. Contracts (self-audit): C transaction, G remote readback, I shift/lease, M runtime, E QA, D scheduler, J route, external evidence tiers. Result: missing full checkout explicitly surfaced; do not invent tests against absent source.
3. Adversarial (self-audit): forged webhook, duplicate GUID, many workers, long Retry-After, stale remote state, SHA mismatch, process death, malicious approval, lost wake and F20. Result: targeted local tests added; L2 and L4 remain not run.

These audits were performed within one assistant session and are not independent reviewer attestations.
