# Studio Communications Data Branch (V1 seed)

Status: INERT / DESIGN SEED. No broker installed. No workers are triggered by this branch.

Authoritative governance: main. Authoritative job/runtime state: studio/runtime. Actual source and QA: owning repositories and exact SHA.

This branch is reserved for append-only normalized communications events, versioned daily digests, and snapshots. Proposed on main at studio/communications/. No private oversight case details belong here.

When activated, only the reviewed broker principal writes accepted events. Workers post structured messages to their assigned job Issues. No force-push/deletion and no unverified state mutation.
