# The Nomad Studio V1 — Current Integration State

This directory is not a migration instruction or an earlier release upgrade
record. The project is the **first** Studio V1 baseline, based on A–S plus
AG–AV source workgroups from **one concurrent implementation wave**. The source
candidate has a unified pinned `schema_v1.sql`. Only fresh initial database
installation and same-V1 restart are supported.

`worker_evidence/` and `receipts/` are retained original *development-era*
worker reports. They are historical provenance, **not** executable gates,
required migration ordering, a remote merge, or a production release record.
Some of those reports describe now-deleted intermediate SQL migration files.
They must not be used to plan a database upgrade or to override Studio V1 source.

Current source verification consists of the full tests in `tests/`, the dynamic
CI source gate, a cold-built and installed wheel, fresh database initialization,
reopening the resulting database, and rejection of non-V1 database layouts.
The actual test results are recorded in the current patch verification log.

**Not proven:** an authenticated user-opened continuous ChatGPT worker runtime,
200/500/800 simultaneous live workers, private Oversight repository source and
issuer, real provider-side IAM and readback, owner release, and independent QA.
No GitHub branch was selected or pushed by this local correction.
