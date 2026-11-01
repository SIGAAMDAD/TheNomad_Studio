# R5R browser-worker setup

Workers are ordinary ChatGPT browser conversations initialized manually by the human owner. Each registered conversation has a `worker_id`. An executing response is a separate `shift_id`; `assignment_id`, `command_id`, lease generation and remote commit identity remain distinct.

## Bootstrap

1. Use the authenticated operator API to register an existing, manually created browser conversation for a permitted project and role. `registerWorker` is registration, not browser launch.
2. Start an actual response in that conversation. Bind its shift, verified project and trusted role identity through the authorized workflow; `beginShift` is a record of observed initiation.
3. Obtain the current project-bound GitHub communications ref and exact addressed mailbox path from trusted R5R state. Check command recipient, payload SHA-256, project, shift, lease and generation before acting.
4. Produce real artifacts or bounded source work under the accepted scope. Publish immutable checkpoint, result or terminal receipts to the configured GitHub ref; record the remote path/commit/digest.
5. Verify the actual remote bytes. Reconcile ambiguous post-write outcomes by exact readback rather than blind repetition.
6. Observe STOP cooperatively and publish an authenticated end-of-shift receipt. Independent QA and owner approval remain separate gates.

## What the coordinator observes

A queued assignment, generated prompt, GitHub notification or persisted database row does not prove an active browser response. The coordinator can publish commands and verify receipts while its service runs, but it cannot awaken a completed/idle browser chat. Do not substitute external scheduled tasks, ChatGPT Work, or paid model API agents for the owner-defined conversation workforce.

## Live acceptance F-20

Observe **one already initialized browser conversation**, within **one continuing response**, receive **two successive authorized GitHub assignments** and publish **two independently verified remote receipts**. Collect worker and shift IDs, lease generations, command IDs, real GitHub blob/commit SHAs, publisher/readback evidence, and actual timestamps. A mock relay or contract test is not evidence that F-20 occurred.

See [Setup](SETUP.md), [Production pipeline](PRODUCTION_PIPELINE.md), and [Owner acceptance](OWNER_SETUP.md).

## Remote evidence verifier

Use `python -m qa.f20_remote --evidence /secure/f20-observed.json` with a
privately provisioned `STUDIO_GITHUB_TOKEN` to independently read back both
immutable GitHub command and receipt files at their exact commit SHAs. See
[Owner acceptance](OWNER_SETUP.md) for evidence fields. The returned remote
readback verdict is distinct from human observation of one continuous response.
