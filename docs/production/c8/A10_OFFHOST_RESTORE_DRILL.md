# A10 — off-host recovery drill (operator procedure; no approval implied)

**Status:** PREPARED, NOT EXECUTED OFF HOST. This procedure is not evidence of a completed
independent restore or a grant to switch SQLite authority from SHADOW to ACTIVE.

## Prerequisites

An owner-authorized operator provisions an independent restore machine and private
local disk (not the primary authority machine or a shared/remote-mounted SQLite
volume). The operator records trusted host/storage identifiers, a controlled incident
start/watermark, the owner-approved RPO/RTO limits and a separate QA reviewer.

The source-side snapshot must be created by `python -m infra.sqlite.backup snapshot
/absolute/private/source.db /absolute/private/snapshot.db` in a private directory.
Transfer the **unchanged** `snapshot.db` and `snapshot.db.manifest.json` together via
an approved secure transport. Verify transfer file hashes on both ends. Never copy
a live `authority.db`/WAL pair as the backup, and never open the restoration target
on a shared production volume.

## Executable drill on the independent destination

1. Create a fresh private directory owned by the executing service account:
   `install -d -m 0700 /absolute/private/restore`.
2. In the deployed source checkout, run
   `python -m infra.sqlite.backup verify /absolute/private/inbox/snapshot.db`.
   A missing or altered manifest is a hard failure.
3. Run `python -m infra.sqlite.offhost_drill
   /absolute/private/inbox/snapshot.db /absolute/private/restore/restored.db
   --source-host RECORDED_SOURCE_HOST > /absolute/private/restore/drill-receipt.json`.
   The destination must not already exist. This command validates integrity,
   verifies all logical row digests, copies the database, forces authority to
   **SHADOW**, checks foreign keys and captures an operator-level timing receipt.
4. Stop on any nonzero exit. Do not retry into an existing target or route live
   work to it. Record the failing command, stderr, file hashes, and attempted
   destination without modifying source authority.
5. Have **independent QA** corroborate source and destination host/storage identities,
   input/output hash readback, version compatibility, collection timing, failed
   side effects, replay/cutover safety, and actual incident start/end watermarks.
   Attach signed owner/QA observations separately; the tool never self-attests them.

## Minimum external acceptance record

| Field | Requirement |
| --- | --- |
| Exact source SHA and schema migrations | Verifiable Git SHA, immutable migration checksums |
| Snapshot identity | SHA-256 of snapshot **and** manifest, snapshot generation/watermark |
| Independent destination | Trusted independent host, volume/device proof, operator identity |
| Readback | Stored receipt plus independently verified integrity, foreign-key, row digests, SHADOW mode |
| Fault boundaries | Document both failed fsync/copy and abrupt pre-/post-manifest exit cases |
| RPO/RTO | Owner-approved targets, measured incident-to-recovery window and observed data-loss bound |
| Rollback | Owner-approved route; no concurrent active writer or unreviewed ACTIVE switch |
| QA and owner approval | Independent exact-source verdict and explicit activation decision |

`SQLITE-RESTORE-DRILL/1` contains only **unreviewed operator capture**.
`off_host_verified=false`, `independent_qa=NOT_RUN`, and
`cutover_authorized=false` are deliberate. Even an operator-supplied different
`--source-host` string is not trusted evidence. RPO and full outage RTO remain
UNMEASURED until external incident markers are independently checked.

**Conditional PostgreSQL transfer:** only run the separately controlled
`infra/sqlite/transfer.py` path after validating that a real PostgreSQL authority
exists. Record `PG_EXISTS=UNKNOWN` until trusted deployment/readback evidence
establishes YES or NO. Do not assert an export happened merely because the code
can perform one.
