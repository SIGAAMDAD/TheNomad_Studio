# WG-K: The Nomad Studio browser cockpit

This is an implementation of the **72 frozen operator operations** in `CONTRACTS/openapi.json`. It is a React/TypeScript Vite app. It does not implement or simulate the API or GitHub connector. The typed backend is owned by J, the real GitHub effects are owned by G, and manual session projection is owned by I.

## Run

From the canonical integration tree (with `CONTRACTS/openapi.json` and `CONTRACTS/api.types.ts` adjacent to `ui/`):

```sh
cd ui
npm install
npm run generate
npm run check
npm run verify-handoff
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
- Browser session operations only produce/query coordinator state. The Manual Session Handoff workbench copies a human-supplied, verified packet for the owner to paste into an **ordinary new ChatGPT browser conversation**. No API operation launches, awakens or stops a ChatGPT browser conversation.
- The frozen `startWorkerResponse` contains only `session_id` and `state`: it does **not** contain the full browser launch packet, lease generation, Canon digest, exact relay paths or GitHub receipts. The UI therefore requires those fields to be supplied from independently verified artifacts; it never guesses them. Similarly no listRelayInbox/readRelayReceipt endpoint exists; operators can open a verified GitHub repository/ref for the immutable relay and inspect observations via listWorkers/listEvents.
- The Bible draft workbench offers **ephemeral local text and UTF-8 SHA256**. The frozen API supports stage-by-existing-digest but not raw blob upload, so editing a Bible does not persist the text automatically. Actual blob delivery must be performed via the separately authorized immutable artifact path before staging.
- Client-side confirmation is not server authority: J/A/M must independently verify actor grants, revisions, owner decisions, and subject/effect digests.

## Dependencies and validation

Selected packages: React + React DOM 19.1.1 (MIT), Vite 6.3.5 (MIT), `@vitejs/plugin-react` 4.7.0 (MIT), `@tanstack/react-query` 5.83.0 (MIT), TypeScript 5.8.3 (Apache-2.0), `@types/react`/`@types/react-dom` (MIT). React Query was chosen to scope and invalidate server projections; no second browser persistence layer is added. Actual resolved package hashes, CVE audit and OS clean-install remain **NOT_RUN** where registry access is unavailable. A lockfile must be produced with `npm install --package-lock-only` in a network-enabled environment, retained in the canonical integration tree, and verified with `npm ci` before a reproducible production build can be certified. Pinning exact package.json versions is not proof of a reproducible dependency install.

The offline contract-generator unit tests use only the Node built-in test runner and verify 72/72 coverage, route identity, no invented inference/worker invocation, path encoding, error envelopes, mutable-request UNKNOWN handling, project separation and manual handoff constraints. Real React build and browser interaction require installed dependencies and the actual J backend; report them separately as NOT_RUN if unavailable.
