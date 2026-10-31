# Repository hygiene and branch ownership — R5R

Audit date: 2026-10-31. The implementation branch contains 1,244 tracked blobs (approximately 5.2 MB of uncompressed content), with zero empty files and no tracked Node dependencies, Python bytecode, built distributions, coverage databases, ZIPs or SQLite state files.

## Branch roles

- main: protected reviewed source and frozen contracts.
- studio/runtime: mutable machine job records and historical receipts; preserve.
- studio/comms: mutable communications branch and immutable-addressed relay history; preserve.
- implementation/r5r-student-one-wave-20261030: integration branch; consider deletion only after its PR has merged and the target commit has been independently verified.

## Cleanup already made

The root README and superseded V2/V3 ChatGPT scheduling, owner setup and production pipeline documents have been replaced with R5R configuration, authority and pre-pilot instructions. An early source-only R5 deployment handoff was also refreshed.

## Intentionally retained

- QA evidence logs and past submission proofs; these are tracked audit records, not accidental runtime log output.
- Specialist role and department manifests, including copies needed by independently installable skills.
- Historical studio/v1 and hyperscale architectural materials. Historical versions are references, not live production authority. Do not delete their paths without checking active workflows, imports and user-authored references.
- Both operational GitHub branches and their receipts. Do not rewrite history as a cosmetic cleanup technique.

## Deletion criteria

For future removals, confirm the candidate is unused in current workflows, tests, launch prompts, role loaders and external source contracts. Document exact paths, run the full frozen-contract and CI suites at the resulting commit, and preserve audit history. Do not delete the runtime/comms branches or remove files solely because their names contain v1, backup, or log.

## Remaining external gate

Source CI and code coverage are separate from F-20: real browser worker continuation across two successive addressed GitHub assignments with authentic receipts.
