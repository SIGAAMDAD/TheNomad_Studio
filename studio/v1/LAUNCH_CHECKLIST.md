# Studio 1.0.0 — deployment and smoke-test checklist

The current merged controller is non-spending. Work through these gates in order and record actual evidence:

1. Confirm main/head SHA, studio/runtime and studio/comms refs; **never** merge the data-plane branches into main. Current live config has source_writes_enabled=false, auto_ai_enabled=false and auto_dispatch_enabled=false until the owner separately authorizes activation.
2. Run `python -m unittest discover -s tests -q`; `python scripts/studio.py validate`; `python scripts/production.py validate`; V1 role/mapping validator. Verify CI in a PR at its actual SHA.
3. Register only approved logical chat workers in studio/runtime/studio/chat_workers.json (worker ID, role, head, job, epoch, audit interval); do not seed fake STARTED states. Actual scheduled S00–S09 and event Work W01–W06 task IDs must be read back in the platform.
4. Ensure each worker can read the corresponding inbox and write its department ledger via a verified connector. Test a real approved Issue STARTED -> broker archive -> inbox -> ACK -> ledger CHECKPOINT -> auditor; verify normalized event ID and source comment. Run a negative test for wrong assignment epoch.
5. Pilot one actual bounded creative task and one approved engineering source-only task with independent reviewer. Source CI relay requires separate source audit/publisher GitHub App credentials and actual runs on the five source repositories. Missing configuration is NOT_RUN, not PASS.
6. Run the hourly pulse and verify durable ERROR signals for missing ledger event, overdue heartbeat and outstanding ACK. Ensure the S00 director receives and interprets the report. At 23:00 Central test the scheduled daily report and authenticated Gmail send/readback separately.
7. Configure branch protections and independently authenticated department-head identities. Prove workers W01–W06 cannot merge, then prove head merge is possible only with an accepted QA/CI SHA and owner approvals.
8. Preserve GOAP real source code and immutable history; close or archive only obsolete Studio work-order artifacts after dependency reconciliation. Do not delete `studio/runtime` job receipts or rebase `studio/comms`.

If any gate fails, mark BLOCKED and record exact link/receipt. Do not launch 100 workers against unverified transport/lease gates; scale logical registrations after the pilot.
