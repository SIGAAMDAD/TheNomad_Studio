# TheNomad_Studio

This private repository now contains an opt-in, deterministic control-plane bootstrap for The Nomad's AI production studio. Read `studio/config.json`, `studio/jobs.json`, and `studio/slots.json` before making changes.

## First-time setup
1. Review and merge the bootstrap pull request. Scheduled GitHub Actions run from the default branch.
2. Open the **Actions** tab and manually run **Studio control validation**, ensuring the schema, dependency graph and dispatch preview pass.
3. Source code automation remains **disabled** until the canonical October game/framework/terrain SHA and build toolchains are frozen and approved. The default config explicitly blocks code dispatch.
4. Add owner-approved jobs to `studio/jobs.json`. Jobs require `id`, `kind`, `state`, and optional `dependencies`; for example: `{"id":"DES-001","kind":"design","priority":10,"state":"READY","dependencies":[],"role":"rpg-gameplay-designer"}`. Only mark a job READY after scope, dependencies, ownership and evidence gates are met.
5. Enable desired specialist slots in `studio/slots.json`. Keep S00 reserved for the production director. Run `python scripts/studio.py dispatch` to **preview**, not execute, a reservation. Local `--write` persists to JSON but should only be used through a serialized, reviewable branch change.
6. Update existing scheduled ChatGPT worker prompts to read these GitHub slot/job records as authority, and persist exact checkpoint/QA evidence. GitHub Actions does not automatically wake an ordinary ChatGPT scheduled chat.
7. Implement source analysis/build CI in the three actual game repositories as independent, tested workflows before enabling programming. Do not treat green Studio schema checks as proof that Godot or C++ builds.

## Current upstream branches
- `SIGAAMDAD/The_Nomad:Nomad3D`
- `SIGAAMDAD/NomadFramework:develop`
- `SIGAAMDAD/Terrain3D:Terrain3D_NomadRenderer`

## Automation included
- `studio-check.yml`: validate policy and print a dispatch preview on PR, push and manual invocation.
- `studio-reconcile.yml`: hourly UTC-based read-only dispatch preview, publishes evidence as an artifact; does not update queue or perform AI work.
- `ai-manual.yml`: manual eligibility gate for a future AI executor; intentionally **does not call a paid model**.
- `scripts/studio.py`: dependency-cycle validation, unique assignment checks, source completion gate checks, capacity-limited READY-job reservation planning, optional local JSON persistence.

## AI models inside Actions
It is technically possible to invoke OpenAI/Codex or another model provider from a GitHub Actions job. Model inference requires its own authenticated API/agent entitlement; your ChatGPT scheduled-chat allowance is not automatically OpenAI API credit. The current bootstrap performs **zero inference** and requires **zero model API secrets**. A future explicit opt-in can use an `OPENAI_API_KEY` repository secret, spend limits, scoped permissions and reviewable feature branches. No default-branch auto-merges or unbounded recurring model invocation are configured.

## Local commands
```bash
python scripts/studio.py validate
python scripts/studio.py status
python scripts/studio.py dispatch
```

This bootstrap is deliberately inert with respect to source edits, paid AI spend and automated ChatGPT task creation. It does not supersede the approved Drive baselines.
