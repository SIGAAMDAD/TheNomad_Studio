# WG-B / `gateway` — mTLS authenticated HTTP/1.1 entrypoint (CONTRACT/COMMAND/1)

**Status:** implemented and locally verified using real loopback SSL sockets and temporary OpenSSL CA/client/server certificates. **Not exposed or proven reachable from an ordinary ChatGPT web chat.** WG-P supplies actual secure host/connector deployment.

## Public facade and lifetime

```python
from nomad_studio.gateway.api import build_transport, store_audit_sink, probe_real_connector
transport = build_transport({
    'trust_component': trust,          # WG-B trust concrete facade
    'components': {'trust': trust, 'shift': shift, ...},  # WG module Component handles
    'tls_context': server_ssl_context, # SSLContext verified client cert REQUIRED
    'certificate_identities': {       # SHA256(client certificate DER) -> immutable policy
        'sha256-in-hex': {
            'principal_id': 'owner/one',
            'credential_id': 'registered-mtls-id',
            'issuer_id': 'trusted-ca-identity',
            'scopes': ['trust:issue_launch', 'trust:revoke', 'gateway:health'],
            'expires_at_us': 9999999999999999,
            'authority_epoch': 1,
        },
    },
    'audit_sink': store_audit_sink(state_store),  # WG-A append_event, required
    'host': '127.0.0.1', 'port': 0,      # port 0 only for local tests
    'max_body_bytes': 65536,
    'rate_per_second': 20, 'rate_burst': 40,
    'preflight_rate_per_second': 40, 'preflight_rate_burst': 80,
    'max_inflight': 256, 'io_timeout_seconds': 10.0,
})
(host, port) = transport.serve()  # explicit listener lifetime
transport.close()               # shutdown and join; idempotent
```

`SSLContext` must use a trusted CA, `verify_mode=ssl.CERT_REQUIRED`, verified certificate chain, minimum TLS 1.2 (recommended TLS 1.3), and a real private server certificate. The gateway **rejects TLS-optional/unconfigured startup** and never promotes headers/JSON/prompt `worker_id` to VerifiedIdentity. Client certificate SHA-256 identity mapping is maintained out of band, protected by trusted host configuration. An unknown leaf signed by the CA is still denied. Call `transport.rotate_certificate_identities(new_verified_map)` to revoke old fingerprints for the next request; CA/server key rotation requires a new host-provided SSLContext/restarted listener. A verified connector bridge can use its own narrowly mapped mTLS credential; its real ChatGPT session claims remain unverified unless WG-P supplies independent attestation.

## Wire API

| Endpoint | Requirements | Behavior |
| --- | --- | --- |
| `POST /v1/command` | mTLS + `Content-Type: application/json` + one `Content-Length` | closed `NOMAD/CMD/1` JSON object -> `CommandEnvelope` -> trust admission -> domain `Component.execute` |
| `POST /v1/query` | same; `NOMAD/QUERY/1` | closed query envelope -> trust admission -> domain `Component.query` |
| `GET /v1/health` | mTLS principal with exact `gateway:health` | minimal authenticated `ACCEPTED/OK`; no worker/session information |

The field `operation` must be the fully qualified operation string matching `domain`, for example `domain="shift"`, `operation="shift.next"`. Wire `actor_claim`, `worker_id`, job/shift IDs are **claims only**. Never accept wire `VerifiedIdentity` or arbitrary `scopes`, `role_id`, `issuer_id`, client certificate identity metadata or impersonation flags outside the owner-approved domain-specific launch payload. Per-command `idempotency_key` is required.

After `trust.activate`, a worker includes `Authorization: Session ns1.<id>.<secret>` over mTLS on each job-scoped request. The gateway verifies token digest, current worker epoch, connector binding, current approved slot and scope, then dispatches a freshly minted **attenuated** `VerifiedIdentity` to the sister component. No session can call owner, QA, head merge, auditor, Oversight or publication authority methods. All modules still validate their own data/business/epoch invariants.

### JSON and transport admission controls

- Raw body limit 65,536 bytes by default, configurable lower to at least 1024; missing/repeated/bad `Content-Length`, chunked `Transfer-Encoding` and non-JSON content type rejected.
- Strict UTF-8; reject duplicate object keys at every depth, trailing garbage, `NaN`/Infinity, unknown envelope fields, wrong version/operation pairing, bad identifiers, invalid bool-as-integer, query limit outside 1..500, nested containers deeper than 16, more than 2,048 nodes and oversized strings.
- Bounded concurrent handler slots (`max_inflight=256`, hard configuration maximum 2048), 10-second per-connection I/O timeout by default, and overload HTTP 429 (or 503 when audit unavailable); these are local bounds. TLS handshake rate and handshake timeout must also be enforced by hardened WG-P ingress.
- Per-verified-certificate preflight rate bucket before reading body and per-verified-session/credential dispatch bucket; lock-safe bounded LRU state. Rate limiting is local to each gateway instance (multi-process deployment requires shared limiter at the ingress tier; no claim of global fleet-wide throttling).
- No default HTTP request logging; only `{principal_id, request_id, operation, state, code}` enters sanitized audit port. All domain permission denials/errors audited under verified transport principal. The trust mutation journal separately records actor and job. `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` on responses.
- Secrets are emitted in the **success** payload only for `LAUNCH_ISSUED` and `SESSION_ACTIVATED` on the verified mTLS channel. All other output paths redact common sensitive keys. If audit persistence fails after a downstream mutation, the gateway returns `UNKNOWN/PROVIDER_UNKNOWN`: prior mutation may have committed and must be reconciled by idempotency/epoch, **never retried blindly as proven failed**.

### Failure codes

`MALFORMED_REQUEST`, `INVALID_SCHEMA`, `IDENTITY_UNVERIFIED`, `UNTRUSTED_CONNECTOR`, `INVALID_SCOPE`, `OWNER_APPROVAL_REQUIRED`, `EXPIRED_GRANT`, `REVOKED_GRANT`, `STALE_ASSIGNMENT`, `IDEMPOTENCY_CONFLICT`, `BACKPRESSURE`, `NOT_CONFIGURED`, `PROVIDER_UNKNOWN`, `NOT_SUPPORTED` are passed from trust/gateway; downstream domain codes remain frozen (`QA_PENDING`, `SOURCE_SHA_MISMATCH`, etc.). `BLOCKED` and `UNKNOWN` map to HTTP 503; permission DENIED generally 403; strict JSON malformed 400; rate limited 429; conflict 409; accepted 200. TLS invalid issuer fails **before HTTP dispatch**.

## Connector probe

`probe_real_connector({'real_connector_probe': callable})` invokes the operator-installed, separately evidenced live ChatGPT connector/host roundtrip probe and requires a positive `verified` flag plus independent `readback_ref`. If no probe is configured, it returns `Outcome(state='NOT_RUN', code='NOT_CONFIGURED')`. This function does **not** construct or imply a ChatGPT connector; WG-P is responsible for implementing and validating a *real* supported tool call. A fake probe returning success cannot establish production acceptance: integrator must inspect real provider/source readback. Running this local Python HTTP test proves mTLS, not ordinary ChatGPT web reachability or per-chat identity.

## Integration responsibilities and current limits

- WG-A: single SQLite `StateStore` and durable `StateTx.append_event`; gateway uses `store_audit_sink(store)` while trust handles grant event journal. WG-A applies migrations.
- WG-B trust: `authenticate_session`/`authorize_operation` and `trust.execute` for issuance, activation, revocation, rotation. Gateway never self-authorizes a claimed worker ID.
- WG-P: real host, DNS/reverse-proxy, secure TLS provisioning, CA distribution, connector identity transport, authenticated heartbeat/monitoring and real end-to-end probe. Do not expose a public unauthenticated listener or assume localhost can be reached by ChatGPT web.
- Other WGs: module `Component` handlers in `components` map, domain-specific permission, authoritative leases/reviews/source/readback. Missing component is `BLOCKED/NOT_CONFIGURED`, not mock success.

The local test fixture creates temporary client/server/CA keys and removes them after tests; no private key or fake permanent credential is bundled. Production is **NOT_CONFIGURED**. `probe_real_connector({})` has been run and produces `NOT_RUN`, as required.
