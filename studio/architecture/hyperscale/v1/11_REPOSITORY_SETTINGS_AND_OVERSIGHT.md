# GitHub repository settings — separate Studio and Oversight security domains

Status: AUDIT + RECOMMENDATION ONLY. No repository settings were mutated. GitHub connector cannot administer rulesets/branch protections.

Live readback October 27, 2026:
- SIGAAMDAD/TheNomad_Studio and SIGAAMDAD/TheNomad_Studio_Oversight are PRIVATE, default main, Issues on, no wiki or discussions, allow_squash_merge/allow_merge_commit/allow_rebase_merge true, auto_merge false, delete_branch_on_merge false.
- Studio main, studio/runtime, studio/comms and Oversight main, oversight/cases show protected:false in branch listing.
- Both rulesets APIs return 403: upgrade to GitHub Pro or make repository public. Do NOT make private repos public. Branch protection GET returned 403 integration denied, so no conclusion about inspectable rules beyond branch protected flag. Actual Actions account settings are not exposed by this connector.
- GitHub Docs: private repository rulesets and protection require eligible plans, while actual enforcement also depends on actors, app bypass rules and review settings.

## Studio repo settings target
1. Keep private; obtain eligible GitHub plan / authorized admin settings access. In Settings -> Rules -> Rulesets or Branches, protect main: require PR, independent review, exact current required checks, no force pushes/deletions, stale-review invalidation and CODEOWNERS on governance, workflows and policy. Maintain owner-approved department-head merges only when independently authenticated.
2. Protect studio/runtime with tighter verified controller/bot write scope, owner approval for policies, role/worker registry, budget and lease changes. Immutable department ledgers still require a narrow authorized append publisher. Never give ordinary worker tokens bypass.
3. Protect studio/comms so only the verified broker/batch manifest publisher writes. Restrict deletion/force and validate append-only event semantics; rulesets alone are not an external immutable vault.
4. Use least-privilege GitHub App identities: source CI read, Studio controller, source branch publisher, QA reviewer, Oversight issuer. Protect cross-repo dispatch with actual source SHA readback and narrowly scoped tokens.
5. Settings -> Actions -> General: default GITHUB_TOKEN read-only, explicit write permissions per trusted job, vetted allowlist/pinned Action versions, no blanket workflow PR approvals, environment approval for deployment. Protect self-hosted runners from untrusted PR code/secrets. Secret/code scanning and dependency alerts subject to plan/availability.
6. Keep existing merge modes until a reviewed policy selects consistent semantics. Do not rebase/rewrite existing audit history or enable auto-merge merely to speed up production.

## Oversight repo settings target
1. Keep PRIVATE; membership and permissions stricter than Studio. Ordinary Studio workers and the general Studio GitHub App must not read or write case data.
2. Protect main through independent security/code-owner reviews and required crypto/check suite. Protect oversight/cases as append-only ciphertext with an Oversight-only publisher credential; no force/delete; independently signed external hash anchor.
3. Never store case plaintext, reporters' identifying details or encryption private keys in GitHub Actions logs, Drive shared folders, Studio SQL, worker prompts or ordinary bot identity. Separate encryption/decryption custody and a scoped signing service; ideally independent operator account.
4. Enforced corrective measures must verify an actual current owner-approved decision/epoch and replay protection. A signed notice referencing a plausible URL is not proof of owner approval.
5. Verify with negative tests using a separate lower-privilege test identity: cannot read cases, push protected branch, alter secrets, bypass review or forge signed notice.

## Manual deployment and proof
Do not assume a policy document enforces settings. Owner/admin must configure both repositories in GitHub UI (or a separately authorized admin automation), then record verifiable settings readback, actor-grant table, prohibited-action tests, CI checks and change audit in Studio/Oversight ledgers. Do NOT share a PAT, secret or private key in a chat. Keep source_writes_enabled and auto_ai_enabled false until separately authorized.

References: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/managing-rulesets-for-a-repository ; https://docs.github.com/en/actions/reference/security/secure-use ; https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
