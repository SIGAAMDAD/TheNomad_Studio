# R5R browser-worker setup

The workforce is made of ordinary ChatGPT browser conversations initialized manually by the owner once. This is neither an hourly ChatGPT scheduled-task pool nor paid API inference.

A worker_id denotes a registered conversation; a shift_id denotes one actively executing response; assignment_id, command_id and lease generation identify addressed work separately.

For an authorized shift:

1. Verify the exact project, role, principal, current lease and HELLO record.
2. Read the project-bound GitHub communications ref and immutable addressed command bytes.
3. Verify the command path, payload digest, worker, shift, and lease generation before work.
4. Perform bounded authorized work and publish genuine immutable checkpoint/result receipts to the configured GitHub ref.
5. Let the control plane reconcile actual remote bytes and receipts; chat text and database rows do not establish execution.
6. Observe STOP and publish a terminal shift receipt. On uncertain network mutation, read back the exact path/hash before any retry.
7. Keep source merge, independent QA and canon/release publication under their explicit owner gates.

## Live acceptance F-20

Observe one already-initialized ordinary browser conversation process two successive GitHub assignments *within a single continuing response* and produce two authentic remotely verified receipts. Record actual worker/shift IDs, lease generations and GitHub commit SHAs.

The controller can publish and reconcile commands but cannot itself start or awaken a browser response. If the runtime cannot support this behavior, record an external capability blocker instead of substituting Work, scheduled tasks, Codex or model API agents.

See OWNER_SETUP.md and PRODUCTION_PIPELINE.md.
