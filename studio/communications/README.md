# Communications V1 — proposed architecture

DESIGN ONLY. Nothing here activates AI tasks, grants source-write permissions, or changes the Studio runtime.

Production governance stays on main. The authoritative job queue, leases and V4 department ledgers stay on studio/runtime. Source changes stay in the actual game repositories. The new studio/comms branch is an append-only communications data plane, not a work-order branch or code branch. The private TheNomad_Studio_Oversight repository owns confidential investigations.

The goal is 40–50 persistent logical chat-worker identities. Ordinary ChatGPT chats can participate while active; a GitHub commit cannot wake a dormant ordinary chat. Scheduled chats can retrieve outstanding messages at their actual audit times. Worker state in a report means GitHub-observed evidence, never direct observation of a dormant chat.

Workers publish typed candidate messages on the Issue associated with an existing approved job. A deterministic broker checks the real job and source permissions, verifies GitHub transport provenance, deduplicates events, and serializes accepted events onto studio/comms. Workers never need to push to the same branch.

See BROKER.md, SECURITY.md, WORKERS.md, DEPLOYMENT.md and schemas/normalized-event.v1.schema.json.