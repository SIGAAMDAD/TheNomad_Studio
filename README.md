# TheNomad_Studio — AI game-development control plane

The Nomad Studio uses an **owner-controlled GitHub Project/Issue board**, deterministic GitHub Actions, ten existing scheduled ChatGPT task identities, and original Google Drive/source repositories to coordinate specialists across design, narrative, art, animation, audio, engineering, integration, and QA.

## Start here

- [End-to-end production pipeline](docs/PRODUCTION_PIPELINE.md): feature intake → scoped plan → department work → real artifacts → independent QA/rework → owner acceptance → finalization
- [Owner deployment checklist](docs/OWNER_SETUP.md): actions permissions, trusted approval labels, private Projects V2 token, GPT worker bindings and source/art gates
- [Director contract](studio/DIRECTOR_CONTRACT.md) for S00
- [Specialist worker contract](studio/WORKER_CONTRACT.md) for S01–S09
- [Feature spec example](studio/specs/EXAMPLE.json)
- [GitHub Issue template](.github/ISSUE_TEMPLATE/feature.yml)

## Three distinct truths

| Surface | Authority |
| --- | --- |
| GitHub Issues/Projects | Owner feature scope, priority and decision |
| `studio/runtime` branch | Feature/job states, dispatch epochs, immutable receipts, QA |
| Original repositories / Drive revisions | Actual code, art, documentation, source and accepted canon |

There is **one** parent `FEATURE-N` for Issue N. The scanner checks repository Issues every 15 minutes; optional Projects V2 metadata requires a separate `STUDIO_PROJECTS_TOKEN` and `studio/runtime/studio/board.json.project_url`. No model inference occurs in GitHub Actions. A scheduled ChatGPT worker wakes on its own schedule to read assigned state; a GitHub event cannot directly wake an ordinary ChatGPT task.

## Default safety

The Studio does **not** merge source branches, grant canon acceptance, use paid API/Work/Codex, or create game-source files while `source_writes_enabled=false`. An approved feature alone does not override the accepted source SHA/build/ABI or active ownership gates. Art/animation/audio/UI requests require actual file/manifest evidence, not a written brief masquerading as an asset.

The initial permanent Director is S00; worker slots S01–S09 are dynamically routed across on-demand professional manuals and limited to safe concurrency. The default bootstrap allows two writers and one independent QA reviewer, with no implied 174 parallel task executions.

## Local validation

```bash
python -m unittest discover -s tests -q
python scripts/studio.py validate
python scripts/studio.py status
python scripts/production.py validate
python scripts/metrics.py
```

Controller CI proves software invariants, **not** game compilation, live image/mesh production, authenticated Projects field access, or completed scheduled tasks. Verify each independently.
