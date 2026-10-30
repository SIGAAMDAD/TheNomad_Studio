# Studio operator and worker-shift contracts

The Studio's worker is an existing, human-enrolled ChatGPT browser conversation.
A shift is one human-started continuous assistant response in that conversation.
One shift may process many successive GitHub assignments and inter-worker
messages without returning a final ChatGPT answer between assignments.

## Worker operator operations

| Operation | Meaning | Externally observed evidence |
|---|---|---|
| `registerWorker` | Register the existing browser conversation once, independently of tasks | Verified owner enrollment; state `registered` |
| `beginShift` | Record a human-started shift candidate | State `awaiting_hello`, **not** observed execution |
| `listWorkers` | List durable conversation identities | Current project-scoped registry rows |
| `stopShift` | Queue a STOP instruction into the durable GitHub command outbox | State `stopping`, not forced browser termination |
| `observeCommandReceipt` | Project an independently verified GitHub command receipt | Matching project, worker, shift, command, GitHub commit and digest |
| `endShift` | Finish a shift after verified final receipt and no unfinished commands | State `finished` |

`observeHello` is a trusted internal coordinator ingestion operation, not an
operator HTTP endpoint. It verifies an immutable GitHub HELLO record for the
specific worker and shift before a shift becomes `active`.

## Idempotency and external-effect boundaries

- Every worker mutation is executed through the persistent workforce transaction
  service. A `(project_id, operation, request_id)` and payload SHA-256 uniquely
  bind the original result. Repeated identical calls return persisted result
  bytes; different payloads using the same identity fail closed.
- The API must **not** open an independent outer SQL transaction around a
  workforce command whose domain service owns its complete durable transaction.
- Assignment and STOP command creation update the database and append a command
  outbox item **atomically**. This does not imply remote delivery.
- A separate command publisher observes GitHub's immutable file and exact
  digest. Ambiguous writes stay UNKNOWN and require readback. A published
  command does not prove that a browser worker is active.
- Worker receipts require independently verified GitHub bytes and matching
  address/shift/command identity. The final shift receipt is separate from
  individual assignment results.
- Owner protected decisions, QA approvals, source integration and tool grants
  are distinct trusted authorities. A GitHub credential alone cannot establish
  the identity of a specific ChatGPT conversation or human approval.

## Current integration evidence

`openapi.json`, `operation_catalog.json`, `operation_semantics.json`,
`api.types.ts` and `ports.pyi` define the same **72 operator operations**.
Their syntactic compatibility can be verified locally. Physical APSW,
GitHub transport, .NET and actual browser-worker acceptance require their
respective integrated and live-provider tests. Legacy task-bound SQL rows still
referenced by other production subsystems must be retired as part of full
source integration; their existence is not a future architecture requirement.
