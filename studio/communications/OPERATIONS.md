# Studio communications — operating contract

Status: implementation on main or the reviewed PR; execution depends on actual GitHub workflows and runtime registration.

## Before a chat can publish

1. Register the logical worker in `studio/runtime:studio/chat_workers.json` with worker_id, department_id, role_id, approved job_id, assignment_epoch and audit_interval_minutes.
2. Ensure the job exists in `studio/runtime:studio/jobs.json`, has approved=true and is associated with an existing Studio Issue. `FEATURE-N` maps to Issue N.
3. Use the same connected GitHub owner identity while in the initial shared-connector mode. The resulting event is archive-only and cannot independently authenticate the chat.
4. End substantive work with the existing V4 department ledger event; add a communications message only for useful checkpoints, inter-worker dependencies, handoffs or blockers.

## Message posted as an Issue comment

```studio-message
{
  "schema_version": 1,
  "job_id": "FEATURE-6-G00-01",
  "worker_id": "CW-ENG-001",
  "assignment_epoch": 1,
  "role_id": "rpg-ai-programmer",
  "event_type": "CHECKPOINT",
  "recipient_department": "engineering",
  "recipient_job_id": "FEATURE-6-G00-01",
  "summary": "Reference fixture inventory committed; remaining tests listed in source PR.",
  "requested_action": "NONE",
  "requires_ack": false,
  "evidence_refs": []
}
```

Replace the job, worker, assignment epoch and evidence with actual authorized values. Do not post a synthetic fixture as a real worker update. `source_ref`, when supplied, must identify the job's real source repository and a 40-hex commit SHA; the broker currently records it as NOT_RUN pending independent verification.

## Broker commands

- `event`: ingest one issue_comment.created event from the GitHub Actions event file.
- `reconcile`: scan GitHub comments on approved job Issues to recover dropped events.
- `digest`: write versioned daily summaries without altering or deleting raw events.
- `snapshot`: write an append-only compact worker audit snapshot, including registered but unseen workers.
- `audit`: report latest observed messages and evidence freshness without creating a new snapshot.

The GitHub Actions broker runs on Issue comments and at :17/:47 UTC each hour. A previous-UTC-day digest is generated at 06:30 UTC. An ordinary ChatGPT conversation is never awakened by an event.

## Signals and failure handling

- `UNVERIFIED_SHARED_OWNER` / `REGISTRY_MATCHED_SHARED_CONNECTOR`: claimed worker ID is advisory because chats share GitHub credentials.
- `ARCHIVE_ONLY`: message never authorizes source mutation, QA PASS, canon, workflow activation, spending, or a write lease.
- `NOT_OBSERVED`: no recent message; not evidence that a chat is stopped.
- `OVERDUE_EVIDENCE`: latest reported activity is older than the approved audit interval.
- Missing/incorrect receipt: reconcile from the original Issue and verify event ID. Never forge a replacement SHA.
- Case confidentiality: never use the Studio Issue or comms branch for allegations; use the Oversight sealed intake when separately authorized.

Controller and workflows must remain branch-reviewable. Protect `studio/comms` against force pushes/deletion and keep separate hash backups. A Git history alone does not provide irreversible retention.
