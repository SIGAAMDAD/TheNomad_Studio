# WG-A - contract freeze submission for independent review

**Status:** implemented and locally tested, **not** independently reviewed, owner-approved, live-deployed or remotely committed.

**Reference source:** `inputs/TheNomad_Studio-main.zip`, SHA256 `e46d625a08e49c2f3d003d1c440dabb41863f2c5e66b503d3538694abe9e89ea`; archive reports main commit `8d0b9002e1fedababe72fceffe2a8d6f4bbb3fcd`. The live GitHub main SHA was not queried and cannot be inferred from this snapshot. Oversight was read-only reference; no Oversight files edited.

**Scope:** new `studio/v16/contracts/**`, `studio/v16/schemas/**`, `tests/v16/contracts/**` and `tests/v16/__init__.py`. All other source and history left intact. This patch introduces offline JSON Schema authority, database transaction, manual run and publication protocol with conservative reference negative fixtures. Nothing in these files initiates model work, authorizes source publication, creates credentials, changes branch policy, starts a database, or changes runtime routing.

**Frozen candidate:** `FREEZE_MANIFEST.v1.json` gives each interface path, exact SHA256 and reproducible aggregate SHA256. It is a **candidate** until owner/architect authorization and exact-hash independent QA. Read `00_CONTRACT_INDEX.md` before implementing WG-B/C/D/E/H. In case of conflict with approved source/canon/owner authority, stop and escalate rather than invent upstream behaviors.

**Validation commands:**

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests/v16/contracts -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -q
```

Tests use `jsonschema` + `referencing` (available in the WG-A local environment). They validate closed schema examples, invalid/unknown-version fixtures, spoofed and stale assignments/leases, path variants, synthetic duplicate-command and ACK behavior, source/Drive readback drift and no automated AI launch capability. Tests are **synthetic contract evidence**, not live gateway permission, deployed PostgreSQL, provider write, load test or independently attested QA.

**Open acceptance/dependency gates:** live Studio main head/checksum comparison; owner approval of interface shape and cutover policy; independent exact-digest WG-H/QA review; WG-B/C/D/E/H implementation and actual negative integration tests; no true isolated publication principal or credential setup yet, no production authority. **Next actor:** independent Head/QA checks `FREEZE_MANIFEST.v1.json`, verifies base vs live main, replays local binary patch and tests, obtains owner approval, then advertises the reviewed contract digest to WG-B/C/D/E/H.
