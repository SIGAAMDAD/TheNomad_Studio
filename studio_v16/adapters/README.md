# WG-F / Wave 2 boundary

`DurableRuntime` is a concrete WG-C-based shadow projector, ACK/command adapter,
resume persistence wrapper and bounded report-card generator. It requires an
in-process authenticated `GatewayContext` factory and never starts a model.

`PostgresAuthority` is fail-closed for publication because WG-C's frozen schema
has no canonical `PUBLISH-V16/INTENT/1` durable record and does not contain the
verified issuer/QA/source policy/readback data required by WG-D `State`. The
missing transactionally bound fields are: intent JSON + exact hash, principal,
verified grant and decision/issuer proof, approved work branch, independent QA
subject + CI evidence digest, source and document write-mode flags and trusted
provider observation identity. Implement these in an owner-approved WG-C schema
migration and a server-held evidence/identity registry before enabling `load`,
`reserve`, `recorded` or `reconcile` for live publication. The current frozen
schema is not sufficient to implement their atomic contract correctly.

Do not treat a shadow import, chat-provided context or legacy receipt as an
owner decision. There are no deployed identities or production credentials.
