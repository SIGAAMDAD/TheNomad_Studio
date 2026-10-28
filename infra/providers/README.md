# WG-G isolated publication adapters

`IsolatedGitProvider` consumes a fixed repository-to-remote mapping supplied
by a private service, never worker-provided URLs. It inspects actual Git patch
bytes in a clean clone, performs a base-commit CAS push to an approved work
branch only after an in-process trusted authorization callback, then reads
back commit parent, SHA256 of tree, idempotency and artifact trailers. Default
is read-only. No owner token is read or sent to worker code.

`DriveRevisionProvider` requires a separately implemented *atomic* provider
`update_if_revision(file_id, expected_revision, bytes, idempotency_key)` and
server-side credentials. When Google Drive CAS semantics cannot be proven for
the chosen file type/API, publication MUST stay disabled. A changed revision
or uncertain partial write is not retried until independently read back.

The frozen WG-C/WG-D intent-persistence and authorization seam is pending:
these adapters are not deployable until the gateway can store/retrieve the
full canonical intent under the same transaction as reservation and verify
approved source/QA/CI revisions. Do not run them from a GitHub Actions job
with shared owner credentials or from a manual worker chat.
