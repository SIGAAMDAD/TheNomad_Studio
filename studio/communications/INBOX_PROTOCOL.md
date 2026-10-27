# Recipient inbox and ACK protocol — implementation V1

The comms broker normalizes trusted-transport GitHub Issue comments and archives each accepted message to `studio/comms/events`. The projector `scripts/studio_inbox.py` derives replaceable read views under `studio/comms/inboxes/current/<CW-ID>.json` and `studio/comms/inboxes/unassigned/<JOB-ID>.json`.

These files are **caches, not authorization records**. The immutable event stream remains authoritative for message history. The runtime jobs and leases remain authoritative for worker assignment. Shared-owner GitHub connector identities are not independently authenticated.

## Delivery

A sender must be registered and bound to an approved job/assignment epoch (when the chat registry is enabled). The message includes `recipient_job_id` and may include a specific `recipient_worker_id`.

- Explicit recipient: deliver only when that worker is currently ASSIGNED to the addressed job and is not the sender.
- No explicit recipient: fan out to all ASSIGNED workers for the addressed job, excluding the sender.
- No eligible recipient: preserve the message in `inboxes/unassigned/<JOB-ID>.json`, do not drop it.
- A registered worker's current view contains all archived messages addressed to its current job, `pending`, `acknowledged` counts, exact event IDs, evidence references and source verification state.
- Worker deactivation or reassignment removes only stale derived inbox views. It never modifies an original event.
- Current inboxes must be reprojected from raw messages whenever the registry changes.

## ACK semantics

An ACK is its own approved-job, properly scoped `studio-message` Issue comment with:
- `event_type: "ACK"`
- `causation_id` equal to the source `evt-...` ID
- `requires_ack: false`
- `job_id` equal to the recipient's currently assigned job
- `recipient_job_id` equal to the original sender's job
- `recipient_worker_id` equal to the original sender's logical worker ID when known

The projector accepts the ACK only if it comes from a worker eligible to receive the original message, is not the original sender, and is scoped back to the sender's job. A duplicate ACK does not generate a new delivery. An orphan or forged ACK is counted as invalid; it cannot clear an inbox. An ACK means consumed, not QA PASS, job completion, verified source or owner approval.

## Executable checks

The `studio-comms-check.yml` workflow tests event normalization, duplicate-safe archives, explicit routing, inbox projection, tampering rejection, pending->ACK, invalid ACK, and reassignment. The broker now runs the projector in the same serialized GitHub Actions job as event archiving, so inbox and raw events are committed together.

## Unfinished gates

Independent ChatGPT identity attestation; source SHA verification; permanent backup/ruleset readback; CI evidence relay; registered real workers; and an actual end-to-end GitHub comment->Action->inbox->worker ACK exercise.

An ordinary ChatGPT conversation cannot autonomously wake when this branch changes. An active or scheduled worker must fetch its addressed inbox on its next execution.
