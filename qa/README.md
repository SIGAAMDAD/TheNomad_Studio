# Workgroup L — independent QA / acceptance harness

**Owned scope:** `qa/**`, `tests/acceptance/**`, `tests/unit/l/**` only. This is source implementation, not an approval of the R5 plan or a claim that other workgroups' code exists. It consumes the exact checked R5 plan ZIP and optionally a real WG-M merged source tree, real role inventory, 13 workgroup submissions, local HTTP service and toolchain.

## Invocation

The `qa` module uses Python 3.11+ standard library for offline tests. APSW is optional for the separate APSW reference probe. There is no inference API dependency.

```bash
# From the merged source root, or with PYTHONPATH pointing at this submission root:
python -m unittest discover -s tests/unit/l -p 'test_*.py' -v
NOMAD_R5_PACKET=/path/to/extracted/R5 python -m unittest discover -s tests/acceptance -v
python -m qa.runner --packet /path/to/extracted/R5 --out /tmp/qa-evidence

# After WG-M assembles actual production source:
python -m qa.runner --packet /path/to/extracted/R5 \
  --candidate /path/to/merged/source --submissions /path/to/13-handoffs \
  --role-inventory /path/to/role-inventory.json --role-root /path/to/installed/skills \
  --out /tmp/qa-evidence

# A real two-project local service with *distinct scoped* test principals:
python -m qa.runner --packet /path/to/extracted/R5 \
  --candidate /path/to/merged/source --isolation-config /path/to/project-probe.json \
  --scenarios /path/to/real-scenarios.json --out /tmp/qa-evidence
```

`--allow-commands` explicitly enables configured local process/host/CI invocations. Default behavior never runs arbitrary process commands from a scenario file. HTTP tests invoke the exact configured real endpoints; absent credentials or processes produce `NOT_RUN`, not fabricated success. Tests use an **operator-provided disposable test project** for mutations; the harness does not create production approvals or write external GitHub artifacts by default.

## Evidence model and limitations

The runner emits `results.json`, `results.txt`, and `logs/*.log` with actual command/exit-code/digests for offline reference tests. It records the full actual source tree SHA-256 map, a canonical tree SHA-256, packet plan SHA and frozen contract SHA map. A test `PASS` must have non-empty observed evidence. `FAIL` stops approval; `NOT_RUN` never contributes to pass counts.

**Three evidence tiers are non-interchangeable:**

- **A:** Exact packet, frozen schema/OpenAPI/host/relay bytes, SQLite reference checks, optional APSW *reference*, owned source archives, source scans, source/role/QA artifact hashes. Static source scans are indicators, not proof of total forbidden behavior absence. Tests in `tests/unit/l` prove the QA harness itself, not production Studio services.
- **B:** Actual staged APSW/domain service, authenticated HTTP with two scoped principals, real .NET host/CI and GitHub adapter, source-bound QA review and hostile test cases. `qa/runtime.py` and `qa/blackbox.py` execute configured real scenarios; no fake network fixtures count as B. The optional APSW reference probe is **A**, never B.
- **C:** Human-started ordinary ChatGPT browser sessions, verified GitHub remote readback, human-observed STOP/no wake/manual resume, live project toolchain, source-bound independent QA. The runner does not automatically promote or certify C. A witness must separately run and document the live pilot.

Passing the frozen packet's historical `AUDIT/` tests establishes reference correctness **only**. Five frozen offline acceptance IDs are checked by executable Tier A references; the other 60 original acceptance IDs remain `NOT_RUN` until independently executed against assembled production source. Per-scenario Tier B PASS is a narrower verified probe, not automatic full-requirement closure.

## Worker-submission evidence ingestion

For each `WG-A` through `WG-M`, place `WG-X.zip` (owned changed-source paths only), `WG-X.json` (sidecar), and its raw logs in the same submission directory. ZIP entries must be regular files, no absolute/traversal path, no symlink, no duplicates. A missing group remains `NOT_RUN`. Any present invalid group causes `FAIL` even if other groups have not submitted.

Sidecar minimal schema (not a change to the frozen runtime contract):

```json
{
  "worker": "WG-A",
  "baseline_plan_sha256": "8d68e255008369a299c66ca1a0c253e60faecb2ef46415ad09cbb9b38b41d09b",
  "baseline_source_tree_sha256": "<computed sha256 of canonical empty object, via qa.core.tree_digest({})>",
  "contracts": {"CONTRACTS/openapi.json": "<sha256>", "...": "all pinned contract paths and sha256"},
  "files": {"src/studio/governance/example.py": "<actual sha256 of ZIP entry>"},
  "test_evidence": [
    {"command": ["python", "-m", "unittest", "discover"],
     "exit_code": 0, "log_file": "WG-A-test.log", "log_sha256": "<actual log sha256>"}
  ]
}
```

The verifier rejects any out-of-owner path, mismatched baseline, frozen-contract digest, missing/tampered log, overlapping ownership across workers, or worker files whose merged candidate bytes differ. WG-M is sole canonical integrator; L only verifies integrated source. Its result is byte identity and honest evidence availability, **not** proof the logs were executed on a trusted machine. Separate replay is required for final QA.

The `qa/roles.py` inventory uses real `role_id`, hashed `skill_files` and a `smoke` with exact argv, exit code, log path/hash. Readiness is reported as the actual observed number out of 174; unavailable/missing role bytes are explicitly counted, never autogenerated. A full 174 only passes if all 174 contain matching bytes and smoke evidence.

`qa/receipts.py` validates independent author/reviewer fields and original source/acceptance/validator/artifact bytes. That check alone is **not authentication**. The trusted WG-E and WG-J production services must prove identity/permissions with their own enforcement; Tier B must replay actual effects and verify receipt readback.

## Failpoint and boundary scenario catalog

For each configured real Tier B run, use `--scenarios /path/to/config.json` with:

```json
{
  "format_version": 1,
  "scenarios": [
    {
      "id": "fault-sql-commit",
      "candidate_tree_sha256": "<actual merged source tree digest>",
      "steps": [
        {"type":"http", "url":"http://127.0.0.1:8000/v1/projects/test-project/canon", "expect_status":[200]},
        {"type":"process", "argv":["/path/to/real-fault-injector", "sql-commit"], "expect_exit":[1]},
        {"type":"http", "url":"http://127.0.0.1:8000/v1/projects/test-project/canon", "expect_status":[200], "expect_same_body_as_step":0}
      ]
    }
  ]
}
```

The example is **not executed as a passing test**; replace it with actual service paths, isolated fixtures, command arguments and expected source hash. `expect_same_body_as_step` compares actual HTTP response byte SHA-256 and allows verification that failed writes preserved the old Canon/other unchanged state. Require a valid failure-injection hook *and* post-restart observation; a `false` shell command is not a legitimate injection.

Six specifically tracked fault cases: `fault-blob-stage`, `fault-sql-commit`, `fault-provider-timeout`, `fault-host-kill`, `fault-github-write`, `fault-promotion`. Each requires before-state HTTP, an injected failure (process or HTTP), and after-state HTTP checking exact previous response bytes. Match errors and side effects to `ARCHITECTURE/11_OPERATION_EFFECTS_AND_FAILURES.md`; a timeout is `UNKNOWN` pending authoritative readback, not fake `FAILED`/`PUBLISHED`.

For the native two-project probe, `project-probe.json` contains `candidate_tree_sha256`, `base_url`, `project_a`, `project_b`, `token_a_env`, `token_b_env`. Set those two environment variables to actual *project-scoped Authorization header values* (e.g. `Bearer ...`). The four HTTP calls must observe `A->A=200`, `A->B=401/403/404`, `B->B=200`, and `B->A=401/403/404`. Do not use the owner token for both actors. Actual HTTP response bodies are hashed but never written verbatim; credential header values are never written to evidence.

## Detailed negative test requirements for real integrated scenarios

These cases must be executed against the assembled runtime and not satisfied only by direct SQL insert or dummy provider stubs:

| Check | Required negative and positive observation |
|---|---|
| Multi-project authority (`REQ-038`) | Two independent principals; accessible own project; guessed foreign project Bible/task/approval/blob inaccessible; positive own-project path still works |
| Approval reuse / spoofing (`REQ-015`, `NEG-SELF-APPROVAL`) | Valid trusted owner decision consumed once; same approval fails on a second effect; forged `is_owner` or shared GitHub author is rejected |
| Bible atomicity | Publish/commit interrupted at staged blob and transaction boundaries; old active Bible and Canon remain unchanged; successful authorized publish swaps complete edition |
| Idempotency | Exact request replay returns prior outcome with no second effect; same request ID/different body is conflict; timeout becomes `UNKNOWN` until readback |
| Lease fencing | Correct generation accepted once; stale worker generation and cross-project task rejected; no stolen task update |
| Browser relay | Self-addressed verified `HELLO` enrolls; `NOTE` or foreign `HELLO` cannot; duplicated/corrupted/cross-recipient receipt rejected; polling partial GitHub pages cannot advance cursor |
| Untrusted tool output | Fake owner/role/grant/QA verdict in worker text, GitHub message or tool stdout cannot invoke host or authorize a protected mutation |
| Independent QA | Reviewer is not author; artifact bytes and validator logs match the exact candidate SHA/acceptance; unreviewed PASS and stale base rejected |
| Tool host, provider, CI | Approved signed/hashed executable and grant work; wrong grant/root/symlink denied; provider timeout/GitHub write are reconciled by remote readback |
| Source promotion | Canonical branch ref changed/has conflict => reject stale CAS; only exactly reviewed merged revision promoted; no extra workgroup branch |
| Catalog/skills | 12 departments/174 roles present in real bytes with hashed executable resources and smoke evidence; absent role remains unavailable |
| Scope bans | No paid inference API worker calls, no old Studio compatibility or additional workgroups/waves, no automatic browser session wake/STOP claims |

For HTTP negative cases use scenario `steps` with `expect_status` limited to explicit denial codes. For state preservation capture a GET before the mutation and another GET afterwards with `expect_same_body_as_step`. For idempotent repeat use `expect_same_body_as_step` on equivalent result responses and check durable count/receipt separately. `headers_from_env` obtains secrets at execution time without copying them into reports.

## Integration handoff

WG-M receives this whole changed-files-only archive and imports it without editing frozen contract files. WG-L independently reruns on the **exact** merged candidate SHA, not a worker-provided description or a changed source after test execution. Re-run after every merge that changes that SHA. Do not mark Tier B or C PASS from unit mocks; do not create credentials, policy decisions, branches or owner approvals.
