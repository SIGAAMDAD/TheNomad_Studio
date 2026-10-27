# Communications V1 — deployed deterministic controller

The broker, inbox projector, source CI receipt handler and Director pulse have merged into main. They archive GitHub evidence only; live worker enrollment is empty and external GitHub App publisher/head-authentication gates are pending. Nothing here activates AI tasks, grants source-write permissions, or changes runtime authorizations.

Production governance stays on main. The authoritative job queue, leases and V4 department ledgers stay on studio/runtime. Source changes stay in the actual game repositories. The new studio/comms branch is an append-only communications data plane, not a work-order branch or code branch. The private TheNomad_Studio_Oversight repository owns confidential investigations.

The goal is up to roughly 100 registered logical chat-worker identities (registry schema supports up to 250). Ordinary ChatGPT chats can participate while active; a GitHub commit cannot wake a dormant ordinary chat. Scheduled chats can retrieve outstanding messages at their actual audit times. Worker state in a report means GitHub-observed evidence, never direct observation of a dormant chat.

Workers publish typed candidate messages on the Issue associated with an existing approved job. A deterministic broker checks the real job and source permissions, verifies GitHub transport provenance, deduplicates events, and serializes accepted events onto studio/comms. Workers never need to push to the same branch.

See BROKER.md, SECURITY.md, WORKERS.md, DEPLOYMENT.md and schemas/normalized-event.v1.schema.json.