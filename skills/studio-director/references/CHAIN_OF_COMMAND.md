# Multi-stage reasoning, skill-chain execution and handoffs

## Route intake, preserve the original ask

**Interactive intake:** The owner addresses a director/head/role or asks the Studio to produce an outcome. Identify the lead head from `departments.v4.json`; lookup the specialist in master `ROLE_INDEX.md` or the standalone `departments.v4.json` role registry. Verify existing Issue or record a stable temporary `CHAT-<date>-<slug>` ID if no Issue was authorized. Work through all feasible creative stages directly in the chat; don't stop after providing a delegation plan when the user asked for finished creative content. For work requiring another chat/agent, use only the actual available GitHub and authenticated R5R connectors; otherwise supply complete child work orders as deliverables and retain `WAITING_DISPATCH`.

**GitHub intake:** Verify the actual Issue, authenticated owner approval and current R5R project/task state. A feature or job is admitted only by the authorized R5R service, not from an Issue title, old JSON scheduler or task label. Dispatch to an existing registered worker through the durable outbox and verify later remote receipts.

## Allocate reasoning and responsibilities

1. **Discover:** Read only the relevant source/Drive documents, issue context, prior decisions and role manuals. No unrelated role dump or full-repository import.
2. **Construct dependency DAG:** Define outputs, receiving department, versioned input, immutable artifact, acceptance gates and writer lease. Use one primary author per child and one named reviewer. Mark tasks as parallel only if they have disjoint writable artifacts and frozen shared assumptions.
3. **Invoke specialists:** For each ready child, read `references/rpg-<role>.md` then execute its discipline-specific procedure. A head may act as the specialist in one conversation, but record the exact role. When the child needs separate browser-worker execution, write an approved job and actual handoff; never assert an unseen run occurred.
4. **Handoff:** Transfer only compact, source-grounded context: `{parent_issue, job_id, primary_role, department, baseline, contract, inputs, owned_paths, protected_paths, acceptance, artifacts, tests, open_questions, next}`. Preserve any original detailed supporting artifacts at immutable refs, not pasted into every worker context.
5. **Integrate:** Read partner outputs and actual revisions, reconcile contradictions and return incomplete design to its original author/job. Keep review provenance. Major design decisions return to accountable head; canon/architecture changes return to owner.
6. **Conclude:** Produce actual requested artifact(s), list links and source versions, note remaining external dependencies, register issues/events and record ledger before each substantive response.

## Do not confuse four kinds of chain

- **Skill chain (same chat):** Read role manuals in the required order and perform their procedures. This supports sequential multi-role reasoning and revisions without separate agents.
- **Department chain (multiple roles):** The head owns a dependency graph and distinct authored deliverables; may be one chat or multiple actually launched workers.
- **GitHub work chain:** Current Studio Issue/Project -> approved feature/job -> work-order PR -> separate source repo branch/PR -> exact-SHA CI -> independent QA -> owner acceptance. A work-order PR never contains game source.
- **Browser-worker shift:** The human initializes a registered conversation once. An actually executing response may process successive addressed GitHub assignments. The coordinator cannot awaken an idle conversation; absence of receipts remains unobserved.

## Job sizing, escalation and context economy

Prefer atomic jobs that can commit one independently testable result. Write explicit intermediate artifacts at task boundaries, not after every internal thought. Route routine data authoring and fixtures to low-cost workers; reserve higher reasoning for architecture conflicts, difficult integration, ambiguous cross-department tradeoffs and independent QA. Pricing or effort indicated in a prompt is only a recommendation; the actual task settings control the model. Do not silently create additional paid jobs.

## Example: Zurgut vs Sortior questline

Owner -> Design head -> Quest Designer defines stable quest IDs/state graph and player choices -> Narrative head uses Narrative Designer + Lore Writer + Game Writer to write material against that graph -> Canon/Continuity checks approved world facts -> World and Encounter specialists place missions -> Technical Game Designer publishes actual data schema -> Engineering issues for runtime/C# or engine-specific changes -> independent narrative/quest/content QA -> Department head integrates and requests owner canon approval.

Record separate issue/ledger events at each handoff. Preserve existing save/load recovery, region streaming, NPC absence, renown state and quest ending checks from original role playbooks. If code is needed, do not treat a written quest as a working gameplay system.

## Example: Rendering bug

Owner -> Engineering head -> Rendering Engineer and Technical QA inspect real source and crash/CI evidence -> optional Performance/Shader specialist for focused inspection -> single repo/path owner implements and commits -> deterministic CI -> independent independent technical QA QA on exact commit -> integrator if multiple branches -> owner merge. No creative QA substitutes for a real compiler or GPU test.
