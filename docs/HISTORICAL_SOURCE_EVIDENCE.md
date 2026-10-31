# Historical workgroup evidence lookup

The immutable historical R5 workgroup manifests, source handoffs, old test logs and original pre-pilot workgroup notes remain retrievable from the repository's Git history, although removed from the active source tree in merged [PR #37](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/37).

Historical source anchor: PR #36 head `c9e5be46370f33a3c2e0b6ebddfc89132c7be68b`. Example recovery commands:

```sh
git show c9e5be46370f33a3c2e0b6ebddfc89132c7be68b:host/SOURCE_MANIFEST.json
git show c9e5be46370f33a3c2e0b6ebddfc89132c7be68b:qa/evidence/reference_results.json
git show c9e5be46370f33a3c2e0b6ebddfc89132c7be68b:ui/HANDOFF_MANIFEST.json
```

The 92-case pre-pilot reference matrix required by current tests has a maintained copy at `tests/prepilot/fixtures/R5R_PRE_PILOT_PLAN.md`. Runnable `qa/` test code and current integration suites remain in the source tree.

Old handoff text, historic log counts and detached submission manifests do not certify the integrated/deployed R5R system. Current verification must bind to the intended source revision and independently observed GitHub, Drive, host and browser evidence. Do not reset the `studio/runtime` or `studio/comms` branches to clean up old materials.
