# Optional GitHub Student capacity deployment

This profile never creates or runs AI workers. The existing browser ChatGPT
conversations and their long-running response shifts continue to communicate
through the established GitHub communications branch.

- `STUDIO_CAPACITY_PROFILE=FREE_BASELINE` requires **zero** Student tokens or
  external entitlements. The default is free even if the variable is absent.
- `STUDENT_ENHANCED` enables **only** admission of explicitly observed,
  account-scoped optional resources. `src/studio/operations/capacity.py`
  rejects unknown, stale, expired, wrong-account and insufficient credits.
  It is a prerequisite, **not** a budget reservation or a purchase approval.
- The `.github/workflows/r5r-studio-validation.yml` workflow performs bounded
  Python, TypeScript and .NET verification. It does not infer browser activity.
- `.devcontainer/devcontainer.json` is a disposable Codespaces development
  environment; it must never store canonical SQLite state or be an always-on
  coordinator process.
- Azure for Students is an optional, owner-provisioned **staging** experiment;
  deploy an authorized coordinator and SQLite on persistent volume with verified
  backups, disable paid overage, and rehearse restoring the free deployment.
- Sentry/Datadog/Codecov are optional exports and not required dependencies.
- GitHub Pro benefits are account-scoped. Existing owner-controlled GitHub
  branch and repository permissions remain separate from resource credits.

## Disable and verify

1. Set `STUDIO_CAPACITY_PROFILE=FREE_BASELINE` and remove provider tokens.
2. Run `PYTHONPATH=src python -m pytest -q tests/unit/f/test_capacity.py`.
3. Verify remote GitHub command delivery, current SQLite backups, and source SHA
   with the same baseline; do not promote staging resources into source authority.
4. Tear down optional staging and revoke secrets after readback and backup.
