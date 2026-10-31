# Architecture Enforcer — contributor PR contract

STATUS: REVIEW DRAFT. No active agent, browser-worker shift, source PR, spending, or source-write permission is created by this change.

## Role in the existing studio hierarchy

Architecture Enforcer is a **governance duty**, not a 175th profession or an
additional active paid worker:
- Studio Production Director: inspect job scope and route potential architecture changes.
- Independent Technical QA: inspect actual PR diff, ABI/contracts, source ownership and negative cases independently.
- Principal Engineer (owner-authorized escalation): optional architecture analysis only when the owner requests or the issue warrants escalation.
- Human Owner: sole authority to approve/reject an architecture change and accept source merges.

The enforcer may **block work, request a contract, and initiate an
architecture proposal pull request in the correct SOURCE repository**.
It may not approve its own proposal, alter the approved source architecture
in passing, auto-merge, spend unauthorized credits or override owner decisions.

## Mandatory contributor-style process

If any coding worker discovers that a feature requires *changing* an
existing approved architecture contract:

1. STOP modifications that would violate the current approved contract,
   and checkpoint only safe, independent work. Record the exact source
   commit and reason; mark the original engineering job BLOCKED_ARCHITECTURE.
2. Notify Studio Production Director and invoke the enforcer duty (Studio Production Director triage, independent technical QA independent
   review; optionally authorized technical escalation principal technical analysis). Set the job's
   architecture_impact to "change" and architecture_review.verdict to
   "PROPOSAL_REQUIRED"; this is NOT an owner approval.
3. Determine which repo OWNS the architecture: rendering -> Terrain3D;
   framework -> NomadFramework/develop; core gameplay -> The_Nomad/Nomad3D;
   mod-specific C# -> NomadMain/main; GOAP library -> TheNomad_GOAP/main.
   The Studio repository is NEVER the destination for these proposals.
4. Create an isolated source branch **studio/arch/<JOB_ID>** in that
   owning repository, based on its verified canonical target branch.
   Copy ARCHITECTURE_PROPOSAL_TEMPLATE.md into that source repo at
   docs/architecture/proposals/<JOB_ID>.md.
5. Open a normal **draft pull request** from studio/arch/<JOB_ID> to the
   owning repo's canonical branch. Describe the problem, evidence, exact
   before/after contract, alternatives, compatibility, migration,
   performance and rollback. Include original source/job links and exact
   baseline SHA. A small PoC diff may accompany the proposal only if
   separately authorized; default is a documentation-only PR.
6. Studio Production Director records the **actual source PR URL, number and current head SHA** in
   architecture_proposal in the Studio job ledger; never create a mock
   proposal link. independent technical QA independently reviews risk, source ownership,
   protected paths, ABI, migration and negative cases.
7. The Human Owner responds like a normal maintainer:
   **approve**, **request changes**, or **reject** the proposal.
   Changed proposal HEAD SHA invalidates earlier approvals. An approval
   label, AI vote, or unrelated project board status is NOT authorization.
8. For an accepted proposal, the owner gives an authenticated GitHub
   APPROVED review on the current source PR commit and merges it
   into the correct source integration target. The Studio controller
   must read back the true PR, owner identity, approval SHA, merged
   status and merge SHA. Only then may an architecture_acceptance
   receipt be persisted against the exact source baseline.
9. Studio Production Director rebases/rescopes the original work from the newly approved source
   baseline, updates the frozen contract, and revalidates ownership,
   work branch, leases and compiler/test gates. authorized R5R specialist shifts then continue
   ordinary code implementation; independent technical QA performs separate final QA.
10. If rejected, retain the approved architecture and either implement
    a no-change alternative or cancel the dependent job. Never silently
    apply the proposal's source diff.

For cross-repo interface changes, open **separate source PRs** in each
affected owning repo with explicit dependency order; no one PR is
authorization to modify all repositories.

## Enforced invariants

New code and integration specs **must explicitly declare**
`architecture_impact: "none"` or `"change"`. An omitted impact is not
implicitly "none". The enforcer must review the declared impact
against the actual frozen contract and source diff.

For impact="none", the job must carry an enforcer review receipt
`architecture_review` with reviewer_role="architecture_enforcer",
verdict="NO_CHANGE", and reviewed_baseline_sha equal to the pinned
source baseline. independent technical QA independently inspects the final diff and
protected paths; a worker's self-assessment is insufficient.

For impact="change", code is blocked until:
- `architecture_proposal` points to a real source-repo PR from
  `studio/arch/<JOB_ID>` into that repo's correct target branch, with
  exact matching PR number and current proposal SHA.
- `architecture_review.verdict` is PROPOSAL_REQUIRED and it is bound to
  the job's pinned source baseline.
- `architecture_acceptance` records ACCEPTED plus an actual
  authenticated owner GitHub approval+merge readback, exact reviewed
  proposal SHA and post-acceptance source baseline.
- The normal source freeze, lease, capability, CI, QA, spending, and
  owner acceptance gates still pass independently.

A local JSON acceptance record is **not a substitute** for authentic
GitHub readback. The controller's deterministic validator checks its
shape and relationships, while the executing worker must verify the
live PR/review/merge before acting. The module provides
`verify_owner_readback(...)` for real GitHub response fields but
does not itself fetch credentials or open PRs.

Source files listed under `protected_paths` trigger a deterministic
audit when changed. Semantic API/ABI/ownership changes can occur
outside those paths, so independent technical QA must inspect actual diffs as well. The
existing Studio PR workflow cannot automatically inspect every
external source-repository PR; equivalent source CI/PR checks require
a separately installed authorized integration in each source repo.

## Example: new GOAP predicate needed by Zurgut Hulk

- W01 working on Zurgut Hulk behavior in NomadMain/main discovers it
  needs a planner predicate not supported by the frozen GOAP contract.
- W01 does NOT add a one-off planner change to NomadMain or quietly
  patch GOAP in the same job.
- Studio Production Director splits the dependency. An enforcer proposal PR is opened in
  TheNomad_GOAP/main on branch studio/arch/<GOAP_JOB_ID>.
- Proposal explains predicate scope, reusable semantics, ABI/schema,
  alternatives, other archetypes' interactions and compatibility.
- independent technical QA reviews independently; the owner approves/changes/rejects the PR.
- If accepted, an approved GOAP implementation job is created using
  the new library baseline. Framework adapter gets its own job only
  if needed. The original NomadMain behavior job resumes only when
  the verified dependency is available.

## Operational status

The policy and deterministic checks can be merged to Studio main.
They do not create extra browser-worker shift, enable paid Work, write source
or install external source-repo CI. A native Work worker asked to create
the proposal must have approved repo write permissions; otherwise Studio Production Director
reports a blocker and provides the contributor's exact proposal packet.
The user remains the final architectural authority.
