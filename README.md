> **R5R communications-branch notice (2026-10-31):** The V1 seed description below predates the current R5R GitHub relay design. Follow the reviewed [`main`](https://github.com/SIGAAMDAD/TheNomad_Studio/blob/main/README.md) documentation for current source authority, command/receipt verification and project-scoped APSW state. The branch's existing evidence remains intact; its presence does not establish a live broker.

# Studio Communications Data Branch (V1 seed)

Status: INERT / DESIGN SEED. No broker installed. No workers are triggered by this branch.

Authoritative governance: main. Authoritative job/runtime state: studio/runtime. Actual source and QA: owning repositories and exact SHA.

This branch is reserved for append-only normalized communications events, versioned daily digests, and snapshots. Proposed on main at studio/communications/. No private oversight case details belong here.

When activated, only the reviewed broker principal writes accepted events. Workers post structured messages to their assigned job Issues. No force-push/deletion and no unverified state mutation.
