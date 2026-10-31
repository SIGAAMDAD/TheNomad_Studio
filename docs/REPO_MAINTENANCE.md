# Repository hygiene and source ownership - R5R

## Reviewed cleanup history

- [PR #36](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/36), merged as `4e1d1604208f5ace27207f2ebfa1faff79a64fa6`, removed the ten-slot S00-S09 scheduler, JSON controller state, old scheduled workflows, Work-era worker prompts and dependent scripts.
- [PR #37](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/37), merged as `7d18db7483c69c89cad92183dd598c133f184b41`, removed remaining obsolete intake/policy scripts, timed Apps Script source and historical workgroup handoffs, repaired active skill instructions, and added R5R source-route and mirror checks.
- The merged PR #37 source tree is `9eccfcb5e0d822f4777741b8baab7180b1fd31db`. The historical cleanup inventory recorded 899 tracked files; recompute file totals on any later branch instead of treating that count as current.

## Branch roles

| Branch | Purpose | Allowed changes |
| --- | --- | --- |
| `main` | Reviewed implementation source and frozen contracts | Tested PRs with independent review and owner-authorized merge |
| `studio/runtime` | Operational/historical department events and receipts | Authenticated, correctly scoped operational writes only |
| `studio/comms` | Project-addressed relay records | Immutable, authenticated command and receipt publication |

Neither operational branch is a source-code integration target. Never reset, force-rewrite or delete operational history as part of cleanup.

## Retained source

Keep `src/studio/` (including APSW, source policy, operation handlers and providers), `CONTRACTS/`, `schema/`, `host/`, `ui/`, runnable `tests/`, `qa/`, the 174-role catalog, and independently installable skill mirrors. The 526 catalog entries are content-addressed and have an integrity index. `scripts/verify_canonical_mirrors.py` checks required duplicate distribution files against canonical sources; do not replace them with symlinks or remove them before consumer migration.

The legacy engineering packet helper and review workflow under `scripts/engineering_packet.py` and `integrations/github-actions/engineering-review.yml` remain because not every external source repository's workflow dependencies were accessible for inspection. This is an explicitly retained dependency, not a second Studio scheduler.

Historical workgroup handoffs and logs are retrievable from Git commits; the runnable 92-case pre-pilot matrix remains in `tests/prepilot/fixtures/`. See [Historical source evidence](HISTORICAL_SOURCE_EVIDENCE.md).

## Review and removal standard

Before deleting a file: identify actual imports/build/CI references; inspect external source consumers; preserve exact contractual bytes; update generated consumers and documentation; prove negative behavior and unrelated behavior on one integrated revision. No compatibility layer may recreate the removed ten-slot controller.

A completed cleanup does not mean the deployment scaffolding and live browser pilot are finished. See [Owner acceptance](OWNER_SETUP.md) and [External decommission](LEGACY_DECOMMISSION_STATUS.md).
