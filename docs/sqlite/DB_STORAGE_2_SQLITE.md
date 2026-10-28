# DB-STORAGE/2-SQLITE — DRAFT

Proposed storage realization; owner acceptance and independent exact-SHA QA are
pending. Frozen AUTH/RUN/PUBLISH and DB command/result/event envelopes are unchanged.

One service owns a private local SQLite file. Workers use the independently
authenticated gateway, never database paths or credentials. SQLite uses WAL,
FULL synchronization, foreign keys, trusted_schema=OFF, bounded lock waits and
short BEGIN IMMEDIATE write transactions. Provider requests never run inside a
database transaction. The PostgreSQL implementation remains a compatibility and
historical-transfer reference. Backend configuration belongs to the server.

Identifiers compare byte-for-byte. Digests are lowercase hexadecimal TEXT, JSON
is canonical TEXT, timestamps are signed integer UTC microseconds, money is
integer ten-thousandths converted through Decimal, and booleans are 0/1. Lease
and assignment generations are positive signed 64-bit integers. Schema migrations
are transactional, ordered and checksum locked; destructive downgrade is absent.
Publication patch SHA-256, canonical tree SHA-256 and Git OID remain distinct.

The inherited store surfaces are execute/command, record_event,
reserve_outbox_delivery, mark_delivery_retry, reconcile_outbox, shadow_import,
reconcile_shadow, save_resume_capsule, load_resume_capsule, resume_capsule,
verify_gateway_state, reserve_gateway_intent, authorize_gateway_effect,
recorded_gateway_intent, mark_gateway_conflict and reconcile_gateway_intent.
Publication dispatch/recovery adds begin_effect_dispatch and
reconcile_dispatched_effect. Existing denial codes and wire schemas are preserved.
Only the SQLite state revision prefix changes to sqlite: in trusted evidence.

Authority checks and idempotency reads share the write transaction. Journal,
review, anchor and checkpoint evidence are immutable. Imported historical evidence
remains UNVERIFIED/ARCHIVE_ONLY and cannot create grants. SHADOW is the initial
mode. New effects require fresh ACTIVE authority; readback-only recovery of an
already dispatched effect uses a different trusted principal and cannot write to
a provider. Unknown results require readback, terminal conflicts forbid retry.

The baseline has 25 logical relations (schema_migrations, principals, jobs,
job_dependencies, decisions, grants, worker_assignments, attempts,
resource_leases, event_journal, delivery_effects, inbox_cursors, outbox,
publication_keys, source_publications, document_publications, reviews,
audit_anchors, budget_reservations, recovery_checkpoints, command_results,
resume_capsules, shadow_imports, authority_mode, publication_intents).
effect_dispatches is additive. Department/response/leadership extensions belong
to migration 0003. No confidential Oversight case data belongs in this database.

PG_EXISTS=UNKNOWN: source archives establish no deployed PostgreSQL authority.
No transfer, live provider acceptance, independent QA, off-host backup or ACTIVE
cutover is claimed. Test execution is deferred by explicit user instruction;
static checks do not close checkpoint acceptance gates.
