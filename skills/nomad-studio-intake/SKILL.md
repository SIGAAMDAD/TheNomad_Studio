---
name: nomad-studio-intake
description: >-
  Submit and scope owner-authorized The Nomad game-development features from ChatGPT to the GitHub Studio Issues/Project board, decompose multidisciplinary production into verified jobs and independent QA, and prepare exact owner finalization. Trigger for Studio: build, create project task, plan integration, implement feature, or continue a job.
---

# Nomad Studio Web Intake

This is an **intake client** to the existing Studio Director, not a second scheduler.

1. Read reviewed `SIGAAMDAD/TheNomad_Studio:main/docs/PRODUCTION_PIPELINE.md` and live `studio/runtime` config/features/jobs. Search Issues for the same feature first; do not create duplicates.
2. If needed, create exactly one GitHub parent Issue with original user request, acceptance criteria, source/canon constraints, relevant links, and required output. Verify its URL and number through GitHub readback. GitHub Project drafts must be converted to Issues before production.
3. For execution, confirm an owner-authorized `studio:approved` issue-label event. A status column alone cannot authorize source or creative changes. Never infer canon, paid model, release or protected merge permission.
4. Inspect relevant accepted Drive revisions and Git source. For a source-grounded request, write `studio/runtime/studio/specs/FEATURE-N.json` with bounded jobs (unique ID, kind, role, role_manual_uri, brief, owned/protected paths, dependencies, acceptance, qa_role and exact source SHA for code). Include every necessary design, narrative, art, animation, audio, engineering and independent QA profession; use on-demand role manuals rather than loading all 174.
5. Save through the connected GitHub app, verify real commit/file readback and report the Issue/spec. The 15-minute deterministic scanner admits jobs; existing scheduled ChatGPT workers run on their own clocks. This Skill cannot directly wake them or change their reasoning effort.
6. For follow-up, reuse the same feature, job, branch/PR and checkpoint. Only exact-source QA PASS/REWORK and actually verified CI/Drive/binary asset evidence can advance quality gates. Never treat a generated text brief as an actual mesh, animation or sound file.
7. The owner must apply `studio:accepted` after all children are VERIFIED and `studio:finalized` after authorized publication/merge evidence. Do not silently finalize or merge protected source. Return grounded links and RUN vs NOT_RUN results.

Never expose tokens, create duplicate queues, fabricate SHAs or invent completed tests/assets.
