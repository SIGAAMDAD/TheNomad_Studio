# Source CI relay V1 — evidence and deployment

This Studio workflow is non-spending and only records validated source workflow metadata. It requires separately provisioned GitHub App credentials and a source-repository publisher; no source privileges are activated by merging it.

1. A source work branch runs actual tests/compilation under its owning repository CI.
2. A verified publisher (dedicated GitHub App with contents:write on TheNomad_Studio) delivers repository_dispatch event_type studio-source-ci-v1. Only client_payload keys: schema_version=1, job_id, source_repo, source_sha, workflow_run_id.
3. On TheNomad_Studio, a read-only Source Audit App token is minted from secrets STUDIO_SOURCE_AUDIT_APP_ID and STUDIO_SOURCE_AUDIT_PRIVATE_KEY. Install that App with Actions:read and Metadata:read on the five source repos, NOT source Contents:write.
4. The hub validates the approved runtime job/work branch/repository, asks source GitHub API for the completed CI run, exact SHA and branch, all jobs (<=100), conclusion and artifact index (<=100).
5. The hub commits an idempotent receipt to studio/comms/evidence/source_ci/<repo>/ci-<digest>.json.

Trust limits: GitHub run metadata is verified, but artifact bytes/digests are not downloaded or checked. Source CI PASS is neither independent QA nor owner acceptance. The relay does not mutate jobs.json, source permission gates, canon, work branches, or chat state.

Source publisher is NOT yet installed. In a controlled follow-on change, attach a source workflow_run-completed publisher to TheNomad_GOAP/build.yml with exact work-branch/job matching and separately scoped authentication; do not deploy this in the source repositories while the Studio source-writes gate remains disabled. This is suitable for GCC/Clang/MSVC CI and a future Clang AST/static-analysis evidence packet.

The action fails closed when secrets, source read permissions, source SHA, job assignment, or artifact inventory are absent or inconsistent. This receiver deliberately rejects partial pagination rather than inferring that missing jobs passed.
