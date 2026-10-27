# Studio V1 — department-head-only source merge gate

Only the accountable department head may invoke GitHub source PR merge, after an individually authenticated head GitHub App/token is verified by branch rulesets and head approval policy. The Studio Production Director and W01–W06 are not merge actors. The owner retains architecture, canon, budget, and strategic authorization.

Preconditions:
1. Parent job/child identity and accountable department head are verified from studio/runtime (not PR body prose).
2. The source PR is in the exact authorized repository and target branch; its head equals the reviewed source SHA.
3. Final source commit and matching handoff report exist, current CI passes at that SHA, and an **independent** QA verdict passes at that same SHA.
4. Dependencies and source ownership/leases are satisfied. Architecture/canon changes have a separate, genuine human Lead Director approval.
5. The head uses a distinct authenticated identity; a GitHub token shared by ChatGPT chats cannot prove the invoker's department role.

If any gate is unverified, post MERGE_AUTHORIZATION_BLOCKED, preserve the PR, and include the exact missing requirement. This is a deploy-time source-branch protection condition, not a guarantee created by prompt text.
