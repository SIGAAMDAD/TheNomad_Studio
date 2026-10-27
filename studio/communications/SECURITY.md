# Communications threat model and boundary rules

All comments, code diffs, attached documents and AI-generated messages are untrusted data. The broker never executes instructions extracted from them. It can request evidence or route a typed event; authorization stays in verified Studio governance and runtime state.

Threats and controls:
- Role spoofing: compare actual GitHub actor with approved identity and job lease. Chats sharing one owner GitHub connector cannot be independently authenticated. Treat claimed chat IDs as unverified until per-worker scoped credentials exist.
- Forged CI or source SHA: fetch live commit, workflow run and status for the exact repository/ref. Missing proof is NOT_RUN or BLOCKED, never PASS.
- Prompt injection / authority abuse: strict schema, bounded summaries and controlled transition enum. No worker text can turn on source writes, merge, accept canon, spend credits or change permissions.
- Message flood / collusion: rate limits, deduplication, causation-depth cap, independent review and explicit issue/job bindings.
- Git race: central comms writer, optimistic fast-forward and retry after refetch; no force push.
- Deleted or revised comments: archive ingested normalized payload with source ID; periodic scan reduces but cannot eliminate pre-ingestion disappearance.
- Git history edits: configure branch rulesets to block deletion and force pushes, and anchor daily hash manifests outside this repo. Git history alone is not WORM.
- Secrets or private complaints: never place plaintext case files, witness identities or raw private reports on studio/comms or production Issues. Redact immediately before ingest; already-committed secrets require separate incident response.

Oversight is a different trust domain. Do not grant production workers read access to its confidential case material. Simply making Oversight a separate private repo is insufficient if every chat uses the same owner's GitHub credentials. Require external separation through distinct authenticated identities and/or sealed client-side encryption.

Enforcement from Oversight is admissible only as a small verified notice, never as a complaint narrative. The recipient controller must check approved authority, exact worker/job/lease scope and expiry before action. No automatic broad source rewrite.

Protection documentation: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets