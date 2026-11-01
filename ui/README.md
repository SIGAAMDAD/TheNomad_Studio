# The Nomad Studio operator cockpit

This is an implementation of the **72 frozen operator operations** in `CONTRACTS/openapi.json`. It is a React/TypeScript Vite app. It does not implement or simulate the API or GitHub connector. The typed backend, GitHub effects and worker lifecycle remain separate trusted services.

## Run

From the canonical integration tree (with `CONTRACTS/openapi.json` and `CONTRACTS/api.types.ts` adjacent to `ui/`):

```sh
cd ui
npm ci
npm run generate
npm run check
npm run build
npm test
npm run dev
```

Configure `VITE_STUDIO_API_URL` to override the default same-origin `/v1` service; for local development Vite proxies `/v1` to `http://127.0.0.1:8000` as an **example** operator service address. No mock coordinator is provided. Enter an actual bearer token in the Connection screen; it is kept only in component memory and sent with requests using `Authorization: Bearer ...`. Deploy over TLS and with same-origin or explicitly authorized CORS.

## Important integration facts

- API paths, methods, request field schemas and body shapes are **generated** directly from `CONTRACTS/openapi.json`. `StudioClient.call` is parameterized by `CONTRACTS/api.types.ts`, with no handwritten endpoints. `ui/src/generated/operations.ts` is a checked-in generated artifact; `npm run generate` deterministically refreshes it from the frozen source.
- Each project switch clears query caches, local handoff values and Bible draft buffers. All reads use project-scoped query keys. The authentication token is never sent to GitHub and is not stored persistently.
- All 72 operations have UI access through the nine sections, with generated field editors for path IDs, request body, and available query parameters. Edits are reviewable before submission; owner-gated operations require explicit typed confirmation and an exact digest match when the submitted body carries one.
- Transport/network ambiguity on POST/PUT means **UNKNOWN effect**. There is no automatic mutation retry. The operator can inspect an exact effect ID and invoke reconcileEffect; only the backend may resolve the intent.
- Worker operator commands manage durable conversation enrollment and shift state. `registerWorker` enrolls an already manually created browser conversation and `beginShift` records a separate human-started continuous-response shift. Neither launches a chat.
- The work-shift handoff panel uses separately verified `worker_id`, `shift_id`, Canon digest, contract digest and GitHub mailbox path. It does not require a task ID or lease at shift initiation; the active response processes successive assignments through GitHub.
- STOP is a cooperative relay command, and `endShift` requires independently verified final receipt evidence. A GitHub API write is not proof of ChatGPT browser execution.
- The API exposes 72 operations, but full production deployment requires actual service binding and APSW/CI checks for every operation. UI contract tests alone do not establish backend readiness.
- The Bible draft workbench offers **ephemeral local text and UTF-8 SHA256**. The frozen API supports stage-by-existing-digest but not raw blob upload, so editing a Bible does not persist the text automatically. Actual blob delivery must be performed via the separately authorized immutable artifact path before staging.
- Client-side confirmation is not server authority: J/A/M must independently verify actor grants, revisions, owner decisions, and subject/effect digests.

## Dependencies and validation

Selected packages: React + React DOM 19.1.1 (MIT), Vite 6.3.5 (MIT), `@vitejs/plugin-react` 4.7.0 (MIT), `@tanstack/react-query` 5.83.0 (MIT), TypeScript 5.8.3 (Apache-2.0), `@types/react`/`@types/react-dom` (MIT). React Query was chosen to scope and invalidate server projections; no second browser persistence layer is added. The tracked `ui/package-lock.json` records resolved dependency tarballs and integrity hashes. CI runs `npm ci` to verify the clean install against this graph. External CVE review and the production-host browser test are separate acceptance checks.

The offline contract-generator unit tests use only the Node built-in test runner and verify 72/72 coverage, route identity, no invented inference/worker invocation, path encoding, error envelopes, mutable-request UNKNOWN handling, project separation and continuous shift handoff constraints. Real React build and browser interaction require installed dependencies and the actual J backend; report them separately as NOT_RUN if unavailable.
