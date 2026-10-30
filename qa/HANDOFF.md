# WG-L source/QA handoff — 2026-10-30

**Source baseline:** greenfield empty source tree plus the exact owner-supplied `Nomad_Studio_R5_Executable_One_Wave_Replan.zip` (archive SHA-256 `84ba2f2a7fc038e656684f16aa4f9d75ffc398593703462205369889230e5a4b`; frozen `plan.json` SHA-256 `8d68e255008369a299c66ca1a0c253e60faecb2ef46415ad09cbb9b38b41d09b`). No prior Studio source or external Git branch was supplied or used.

**Owned files implemented:** Independent evidence/status engine, frozen contract integrity/ownership gate, source/role bytes verifier, 13 submission/merged tree verifier, tamper-resistant evidence receipt checker, static prohibited-runtime indicators, optional actual APSW reference SQL checks, frozen policy SQLite reference negative cases, real configurable HTTP and command runner, two-project isolation probe, six tracked crash/failure phases, offline acceptance and unit mutation tests, Tier C witness instructions. No frozen contract files were changed.

**Executed locally:**

- `python -m unittest discover -s tests/unit/l -p 'test_*.py' -v`: **23 passed**, exit 0. Evidence: `qa/evidence/logs/unit.log`.
- `NOMAD_R5_PACKET=<exact extracted ZIP> python -m unittest discover -s tests/acceptance -p 'test_*.py' -v`: **5 passed**, exit 0. Evidence: `qa/evidence/logs/acceptance.log`.
- `python -m qa.runner --packet <exact extracted ZIP> --out <QA evidence directory>`: **16 PASS / 0 FAIL / 75 NOT_RUN**, exit 0. Exact packet reference command logs in `qa/evidence/logs/`; status and hashes in `qa/evidence/reference_results.json`.

The 16 PASS checks comprise 10 direct frozen-plan/reference checks, 5 linked frozen reference acceptance IDs, and one executable SQL negative-case suite. The 75 NOT_RUN checks include 60 original integrated acceptance cases plus 15 extended missing-source/live-evidence checks. APSW is not installed in the execution environment; its reference check is `NOT_RUN`, not `PASS`.

**Deferred integration seams (scoped):** No assembled WG-M canonical source tree or other A-K/M submissions available, so the exact merged source check and real production service/host/API/connector/CI tests have not run. No verified 174-role installed inventory and no real game project/repo/ref/browser pilot credentials were provided, so corresponding checks are `NOT_RUN`. Tier C is not claimed. No remote GitHub commit or promotion was made.

**WG-M import:** Extract owned paths from source ZIP into canonical WG-M tree. Preserve frozen R5 contracts. On assembled bytes invoke `qa.runner` with `--candidate`, `--submissions`, `--role-root`, `--role-inventory`, and configured real scenarios. Independently replay real integration tests and compare their evidence to the *same canonical tree digest* before source promotion. The separate sidecar and log shipped with the archive provide initial digest evidence for this WG-L handoff; a manifest digest does not establish human identity.

**Important status semantics:** Local success proves QA infrastructure behavior and frozen reference contracts, not implementation of missing workgroups, source promotion, live GitHub/ChatGPT activity, or actual independent QA of production candidate bytes. Missing external resources block only those affected checks.
