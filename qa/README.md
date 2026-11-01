# Independent QA and pre-pilot evidence harness

The executable `qa` package is retained for optional local acceptance against
an explicit frozen R5 packet and user-supplied source/role evidence. It does
not make live-provider claims from offline reference tests.

```sh
python -m unittest discover -s tests/unit/l -p 'test_*.py' -v
python -m pytest -q tests/unit tests/integration tests/acceptance
python tests/prepilot/run_pre_pilot.py --help
```

For an offline packet audit, supply your actual packet path and an output
folder to `python -m qa.runner --packet /path/to/packet --out /path/to/report`.
The generated `results.json`, logs and witness records are written to the
output folder; do not commit old immutable WG-L output as current evidence.
A missing runtime, package or remote observation remains `NOT_RUN`.
See `docs/HISTORICAL_SOURCE_EVIDENCE.md` for historical handoff references.

## Authenticated remote acceptance

`python scripts/verify_live_operator.py --url https://localhost:8443` verifies a real
unauthenticated HTTP 401 and an authenticated `/v1/projects` response with a private
`STUDIO_OPERATOR_TOKEN`. Supply an appropriate trusted CA via `SSL_CERT_FILE`
for private TLS installations. This is a running-service check, not an offline mock.

`python -m qa.f20_remote --evidence /secure/f20-observed.json` consumes real
GitHub commit/path evidence and `STUDIO_GITHUB_TOKEN` to verify two distinct
addressed commands and their immutable receipts in the same shift. It checks
remote bytes and SHA256 identities but explicitly does not infer browser
continuity from GitHub metadata. Record independent observation separately.

The remote F-20 verifier additionally requires independently configured
`STUDIO_F20_REPOSITORY`, `STUDIO_F20_PROJECT_ID`,
`STUDIO_F20_CHANNEL_ID`, `STUDIO_F20_SHIFT_ID`, and
`STUDIO_F20_CONTRACT_DIGEST`; these must come from the authenticated
coordinator/deployment, not from the untrusted evidence file.
