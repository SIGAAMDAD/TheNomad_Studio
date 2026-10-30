# WG-K integration handoff (R5 / one wave)

## Ownership and source

- Worker: `WG-K`; changes confined to `ui/**` and `tests/unit/k/**`.
- Greenfield baseline: uploaded `Nomad_Studio_R5_Executable_One_Wave_Replan.zip` plus an empty source tree.
- Frozen OpenAPI and TypeScript contract: `CONTRACTS/openapi.json`, `CONTRACTS/api.types.ts` (read only; **not** modified).
- No source repository/branch or canonical integration tree was supplied to this workgroup, and no GitHub commit was made. WG-M must reconcile these files into the one canonical tree, retaining frozen contracts.
- Source digest manifest: `ui/HANDOFF_MANIFEST.json` (reproducible SHA-256 for the changed owned files, excluding itself).

## Implemented

- Browser React/Vite entrypoint, stylesheet, responsive operator navigation.
- Generated registry containing all **72** HTTP operations, exact methods, paths, path params, query/body/result schemas. Compiler-level `StudioApiOperations` typed client; schema-derived form validation and response envelope validation.
- Operator login via user-provided in-memory bearer token; GETs with no-store semantics; no embedded credentials or browser persistence.
- Query caching scoped by selected project; cache flushed on project and identity changes; Bible text and manual prompt state remounted per project.
- Every operation rendered as either a data inspection panel or a command form (with exact path IDs, request body types, project context and required fields). Server errors show code, HTTP status, related IDs and request ID.
- Confirmation/preview for all submitted changes; explicit typed owner confirmation and repeat-digest verification on owner-gated changes. Existing `request_id` and `effect_digest` are preserved through submission.
- UNKNOWN/network ambiguity for writes; no automatic retry or invented successful delivery. Relay and worker states retain quiet/unknown/observed distinctions.
- Browser START/RESUME handoff prompt requires verified project/session/lease/Canon/contract/mailbox values; copied for human manual launch. No AI service invoked.
- Ephemeral two-Bible workbench to edit UTF-8 text and compute digest before external immutable artifact delivery; no fabricated persisted content.

## Tests and reproducibility

Run in the full source tree (containing frozen `CONTRACTS/`):

```sh
cd ui
node scripts/generate-api.mjs
node --test ../tests/unit/k/*.test.mjs
```

- Generator: PASS, deterministic 72/72 route/operation descriptors.
- `tsc` (strict, built-in core modules): PASS through test-compiled `client.ts`, `catalog.ts`, `form.ts`, and `session.ts`.
- 16 Node test cases: **PASS 16, FAIL 0**. See `tests/unit/k/WG_K_TEST_LOG.txt`.
- TSX parser: **PASS** (12 TS/TSX files, no syntax diagnostics).
- UI check with temporary type shims: **PASS**, but shims are not React's real type definitions and are not certification of a clean React compile.
- Full React/Vite `npm run build`, actual `npm ci`, browser UI/Playwright, live J/G/I integration, GitHub and real project authorization: **NOT_RUN**; npm registry lookup failed with `EAI_AGAIN`, no React/Vite package cache and no service credentials provided.
- Lockfile, exact third-party artifact hashes and CVE scanning: **NOT_RUN**; do not imply npm is reproducibly locked by `package.json` alone.

## Acceptance status (scope-limited)

| ID | Local outcome | Integrated/live outcome |
|---|---|---|
| TEST-REQ-008 | Policy editor typed and owner gated | NOT_RUN |
| TEST-REQ-009 | Bible/CANON UI and two draft workbenches | NOT_RUN |
| TEST-REQ-024 | Notifications/reports/events surfaced | NOT_RUN |
| TEST-REQ-026 | All commands and views exposed | NOT_RUN |
| TEST-REQ-030 | React/TS source written and parsed | NOT_RUN |
| TEST-REQ-037 | Dependencies evaluated in README | NOT_RUN (lock/install/audit) |
| TEST-REQ-043 | Exact record/action preview and confirm | NOT_RUN |
| TEST-TEC-003 | 72 routes generated; static TS client PASS | NOT_RUN |
| TEST-TEC-007 | Error/idempotency/unknown unit coverage PASS | NOT_RUN |
| TEST-REQ-045 | Manual session handoff checks PASS | NOT_RUN |
| TEST-NEG-AUTOWAKE | Negative static/behavioral unit PASS | NOT_RUN |
| TEST-R5-API-CONTRACT | Frozen endpoint parity unit PASS | NOT_RUN |
| TEST-R5-SQL-REFERENCE | Other workgroup scope | NOT_RUN |
| TEST-NO-ARTIFICIAL-BLOCK | Local owned code implemented | NOT_RUN |

## Integration seams and caveats

1. **J**: All authenticated backend operations and project-grant checks are server-owned. No owner decision is inferred from client confirmation. Response schema must match the frozen API exactly.
2. **G/I**: `startWorker` returns `{session_id,state}` only. A full verified browser packet, GitHub immutable relay inbox and receipts are not readable through any frozen endpoint; the cockpit explicitly uses user-provided verified packet values and an authenticated GitHub link. If a richer integrated packet view is desired, it requires an owner-approved contract change coordinated with M. **This UI does not guess paths or prove identity.**
3. **A/G**: Bible raw text upload has no frozen HTTP operation; digest creation is local only, and staging expects externally verified immutable blobs. Project policy GET contains digest/revision but no rule list; a full replacement must come from owner-supplied verified rules.
4. **M**: Provide package-lock from a genuine resolved install on a controlled environment; run a clean `npm ci && npm run check && npm run build && npm test`; test actual browser workflows against J after integration. Resolve all failures before claiming Tier C.
5. **L**: Independently test project switching, API 401/403/409/503, UNKNOWN mutations, stale effects, owner-gated confirmation, relay receipt readback and stopped/quiet browser sessions.
