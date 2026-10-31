# Repository hygiene and branch ownership — R5R

Audit date: 2026-10-31. The implementation branch contains 1,244 tracked blobs (approximately 5.2 MB of uncompressed content), with zero empty files and no tracked Node dependencies, Python bytecode, built distributions, coverage databases, ZIPs or SQLite state files.

## Branch roles

- main: reviewed R5R source and frozen contracts; enforce branch protection in repository settings.
- studio/runtime: mutable machine job records and historical receipts; preserve.
- studio/comms: mutable communications branch and immutable-addressed relay history; preserve.
- cleanup/removal work uses a validated PR and does not rewrite operational evidence.

## Cleanup already made

The root README and superseded V2/V3 ChatGPT scheduling, owner setup and production pipeline documents have been replaced with R5R configuration, authority and pre-pilot instructions. An early source-only R5 deployment handoff was also refreshed.

## Current retention

- R5R Python, API, database, C# host, frontend and canonical contracts remain installed.
- Verified QA evidence and independently installable specialist manuals remain preserved.
- The S00-S09 slot scheduler, its JSON control state, scheduled GitHub workflows and V1/Work-era worker prompts were removed from `main`. Existing operational branch history has not been rewritten.

## Deletion criteria

For future removals, confirm the candidate is unused in current workflows, tests, launch prompts, role loaders and external source contracts. Document exact paths, run the full frozen-contract and CI suites at the resulting commit, and preserve audit history. Do not delete the runtime/comms branches or remove files solely because their names contain v1, backup, or log.

## Remaining external gate

Source CI and code coverage are separate from F-20: real browser worker continuation across two successive addressed GitHub assignments with authentic receipts.
