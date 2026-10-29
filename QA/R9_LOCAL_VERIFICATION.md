# R9 local raw-unit verification record — 2026-10-29

Source authority: ten CAB-A..J submissions reconstructed on pinned local baseline
`237ef363f4206df180b31f1ab8ff147916b9b3660f8f23756eeb31f733356d43`,
then direct seam repairs and the unit campaign in this tree. This is **not** the
remote GitHub `main` source, and its tree content must supersede the historical
server baseline when the new PR is prepared.

## Executed locally

- Exact source-presence / syntax / 56-table+owner-map gate: PASS.
- Deterministic raw-unit sweep: 1,479 tests PASS and 67 subtests PASS; one
  pre-existing pytest collection warning (nonfatal).
- Newly added adversarial cases cover invalid command types; authentication
  digest/proof binding; random DAGs and mutation; source leases and Windows
  drive path injection; dependency and SQLite transaction behavior; and gate
  false-green mutation probes.
- Two code defects corrected from negative probes: unhashable command operation
  no longer raises TypeError; workgraph path normalizer rejects drive roots.
- Python syntax pass: 177 files in the first accepted raw-unit run.
- Security literal scan: PASS (no matching credential literals).
- Local wheel build with no build isolation: PASS.

## Mandatory red gates

- Full integrated runtime test: FAIL to collect because the current `app.py`
  imports deleted V1 trust factory. The P2 R9 root implementation is outstanding.
- Four original P1 TEST-STATE / TEST-FEATURE / TEST-FAULT / RELEASE-OPS
  submissions were not supplied to this integration.
- Trusted provider-native session continuation and host activation: NOT_RUN.
- Independent P3 QA: NOT_RUN. PR exact-commit GitHub Actions: NOT_RUN until PR.
- Live deploy, rollback and production pilot: NOT_RUN.

A synthetic or model-supplied receipt cannot satisfy any of the red gates.
