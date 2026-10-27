# Architecture Change Proposal — <JOB_ID>

> COPY this document into the *owning SOURCE repository* at
> `docs/architecture/proposals/<JOB_ID>.md` on
> `studio/arch/<JOB_ID>`. Then open a **draft pull request** from that branch
> to the owning repository's approved integration target.
>
> Do not open the proposal in TheNomad_Studio. Do not modify the feature's
> existing implementation branch as a workaround. No approved architecture
> changes may be silently inserted into a feature PR.

## Contributor and provenance

- Studio Job: `<JOB_ID>`
- Owner-approved parent issue: `<URL>`
- Source repository: `<SIGAAMDAD/OWNING_REPO>`
- Integration target branch: `<EXACT_BRANCH>`
- Proposal branch: `studio/arch/<JOB_ID>`
- Original implementation work branch: `studio/<JOB_ID>`
- Reviewed immutable source baseline SHA: `<40_HEX>`
- Proposer: `<WORKER_AND_ROLE>`
- Proposed files/interfaces: `<PATHS_AND_NAMES>`

## Problem and observed evidence

Describe the verified limitation blocking the implementation. Include exact
source lines, relevant tests, failure signatures and source SHA. Distinguish
proven failure from possible future improvements.

## Current approved contract

Quote the relevant existing architecture contract in minimal terms. Identify
owners for memory, APIs, thread boundaries, ABI, serialization and lifecycle.

## Proposed architectural change

Describe exactly what would change. Define interfaces, inputs/outputs,
ownership and lifecycle, compatibility and invariants. Provide before/after
contracts and any small illustrative code, but no unapproved broad rewrite.

## Why existing extension points cannot suffice

Demonstrate the smallest no-architecture-change alternative and why it
cannot meet the acceptance criteria.

## Alternatives considered

- No-change workaround:
- Narrow additive extension:
- Full proposed change:
- Expected costs and performance implications:

## Cross-repository dependencies

Identify each affected source repository and canonical development branch.
Create a **separate architecture PR and implementation job per owning repo**
rather than modifying multiple repositories in one unrelated feature PR.

## Compatibility, migration and rollback

Include old and new schemas/ABI versions, expected save/mod compatibility,
donor parity implications, deprecation path, tests and rollback plan.

## Verification

- Compilation and lint:
- Relevant unit/negative cases:
- Game/editor smoke:
- Performance/memory thresholds:
- Not-run checks and why:

## Risk and protected constraints

Document relevant existing architecture requirements and what must remain
untouched (e.g. NMDWorldDatabase memory ownership, Terrain3D donor parity).

## Requested owner decision

- [ ] Approve proposed interface/architecture as scoped
- [ ] Request changes / narrower alternative
- [ ] Reject; retain approved baseline

**No checkbox, PR label, worker comment or draft code is owner acceptance.**
The Studio enforcer must independently read back the authentic GitHub PR,
owner APPROVED review at the exact proposal commit and owner merge into the
correct source target. If the proposal was approved/merged, rebase an approved
implementation job on the new source baseline and re-review its contract.
