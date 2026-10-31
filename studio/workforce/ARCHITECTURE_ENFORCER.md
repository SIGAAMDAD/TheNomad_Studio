# Architecture Enforcer - owner-controlled source proposal process

Architecture Enforcer is a governance **duty**, not an additional profession, an S00 slot or an automatically running model agent. The human owner retains final authority for architectural changes and source merges. The reviewed source-routing boundary is `src/studio/app/source_policy.py`; that source check validates repository/ref/project/commit, not the entire GitHub proposal/approval process described below.

## Responsibilities

- Studio Production Director: classify requested change, route the owning repository, record an exact work dependency and enforce owner decisions.
- Specialist author: identify why the approved architecture cannot support the requested work; checkpoint safely and prepare a proposal, never silently change a protected interface.
- Independent technical QA: review the actual proposal/source diff, ABI, lifetime, ownership, schema, regressions and negative tests.
- Human owner: approve, require changes or reject the proposed architecture; perform or authorize the protected source merge.

## Proposal workflow

1. Stop any source work that requires an unapproved architecture change. Keep unrelated authorized work intact. Record the original job, pinned source commit SHA, candidate impact and failing contract.
2. Select the owning source repository and branch using [Source repositories](SOURCE_REPOSITORIES.md) and the authenticated R5R project binding. Never put game/framework architecture proposals in `TheNomad_Studio` as a substitute for their owning source repository.
3. Where authorized, open a `studio/arch/<JOB_ID>` proposal branch and a **draft PR** targeting that repository's approved integration branch. Use [Architecture proposal template](ARCHITECTURE_PROPOSAL_TEMPLATE.md). Include verified before/after contracts, scope, alternatives, migration, compile and negative tests, and exact source provenance.
4. Record the actual PR URL, number, head SHA, owner identity and independent review evidence. A proposal document, Issue label or worker-authored approval field is not an authenticated owner decision.
5. Require an authentic owner review bound to the current proposal commit and an owner-authorized source merge before treating the architecture revision as accepted. On a changed head SHA, recheck approvals against the new bytes.
6. Pin any follow-on implementation to the newly accepted source baseline and renew its task, lease, protected-path, QA and CI gates. For cross-repository changes, use separately authorized child jobs and PRs for each owner.

When the owner rejects a proposal, retain the current approved architecture and use only an independently accepted no-change alternative or close the blocked job.

## Implementation status and verification

The previous standalone `scripts/architecture_enforcer.py` validator and `architecture_policy.v1.json` configuration were removed in the one-wave cleanup. They are not executable operational authority. The active R5R service provides project authorization and owner decision domains, and the trusted source-policy binding enforces the five approved routing targets. Full cross-repository proposal/review/merge readback still requires an installed authenticated integration; do not represent this written workflow as an automatically enforced PR gate without actual provider evidence.

Review exact source commit, source PR identity, owner review digest and merge evidence before unblocking any architecture-dependent task. Independent QA verifies the implementation after approval; owner approval does not substitute for an actual passing build or review.

## Example: reusable GOAP predicate

A mod-specific C# specialist discovers an unmet reusable GOAP predicate. The Director records a blocked dependency, requests a proposal in `SIGAAMDAD/TheNomad_GOAP` targeting `main`, and obtains separate independent technical QA. Only after verified owner acceptance and source merge may the GOAP implementation be integrated with the framework and the original mod job resume at a newly pinned source revision.
