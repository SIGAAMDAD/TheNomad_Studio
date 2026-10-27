# TheNomad_Studio

Private, deterministic control plane for **The Nomad** AI production studio. The design uses the existing ChatGPT scheduled tasks as bounded workers, GitHub Actions for scheduling/reconciliation, and Google Drive for original creative documents.

## What is implemented

- [Validated ten-slot allocator](scripts/studio.py) with dependency gates, approved job admission, source-write protection, per-slot assignment epochs, maximum writer/reviewer limits, and overlapping writable-path exclusion.
- [Immutable worker receipt protocol](studio/WORKER_CONTRACT.md) that allows hourly reconciliation to advance a partial job from an exact checkpoint or release a blocked slot.
- [Hourly GitHub Actions reconciliation](.github/workflows/studio-reconcile.yml) with a **dedicated `studio/runtime` state branch**; it cannot automatically change the default branch, launch ChatGPT, or invoke paid inference.
- [Deterministic regression tests](tests/test_studio.py) and [validation workflow](.github/workflows/studio-check.yml).
- [Deployment instructions](docs/SETUP.md), including the required manual binding of actual scheduled ChatGPT tasks and engineering source-freeze gates.

## Immediate deployment

1. Review and merge this control-plane change to `main`.
2. Run **Studio control validation**, then **Studio hourly reconciliation** from the repository's Actions tab. The latter initializes `studio/runtime` if needed.
3. On `studio/runtime`, opt into deterministic dispatch using `auto_dispatch_enabled: true` and enable only real worker slots after binding their ChatGPT task prompts. Leave `auto_ai_enabled` and `source_writes_enabled` false.
4. Add a real approved non-code job, and test the complete reservation → worker artifact → immutable receipt → independent QA loop.

Full instructions: **[docs/SETUP.md](docs/SETUP.md)**.

## Authority

- Operations: `SIGAAMDAD/TheNomad_Studio:studio/runtime`
- Game: `SIGAAMDAD/The_Nomad:Nomad3D`
- Native framework: `SIGAAMDAD/NomadFramework:develop`
- Terrain fork: `SIGAAMDAD/Terrain3D:Terrain3D_NomadRenderer`
- Studio source registry: https://docs.google.com/document/d/1HVUomdnsfZv0xb1sFvN1U-eXWpoMwKLVV3IlBBco1RQ/edit

The current engineering source freeze remains pending. No model inference, code generation, source integration, owner approval or API spend is triggered by this repository's deterministic controller. The optional existing `ai-manual.yml` is an eligibility check only.