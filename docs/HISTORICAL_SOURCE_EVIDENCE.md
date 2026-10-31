# Historical workgroup evidence lookup

Original clean baseline: `SIGAAMDAD/TheNomad_Studio` Git commit
`c9e5be46370f33a3c2e0b6ebddfc89132c7be68b` (PR #36).

Historical R5 workgroup manifests, output logs, submitted handoff narratives and
original pre-pilot workgroup notes were removed from the active product tree.
The 92-case matrix consumed by the runnable pre-pilot harness was relocated to
`tests/prepilot/fixtures/R5R_PRE_PILOT_PLAN.md`. They remain
addressable by their original Git revision. For example:

```sh
git show c9e5be46370f33a3c2e0b6ebddfc89132c7be68b:host/SOURCE_MANIFEST.json
git show c9e5be46370f33a3c2e0b6ebddfc89132c7be68b:qa/evidence/reference_results.json
git show c9e5be46370f33a3c2e0b6ebddfc89132c7be68b:ui/HANDOFF_MANIFEST.json
```

These old workgroup reports do not certify the integrated or deployed R5R
service. Current acceptance comes from the exact-head CI and independently
verified GitHub, Drive, host, and browser-worker evidence. Do not rewrite
operational `studio/runtime` or `studio/comms` branches.
