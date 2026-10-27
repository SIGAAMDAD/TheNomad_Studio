# Deterministic Studio hourly and nightly report

Workflow: .github/workflows/studio-director-pulse.yml. Hourly at minute 16 America/Chicago; a separate daily schedule targets 23:00 America/Chicago (local DST handled by GitHub Actions timezone). Actions schedule execution may be delayed.

Inputs: studio/runtime jobs.json, slots.json, chat_workers.json; studio/comms normalized messages, derived inboxes, validated source CI receipt metadata. The report excludes all Oversight case files, witness information and agent private data. It is a compact evidence projection, not a replacement for source QA, ChatGPT worker execution or owner approval.

Outputs on studio/comms: immutable reports/hourly/YYYY/MM/DD/<timestamp>-<digest>.json, and reports/daily/<Central-date>/<timestamp>-<digest>.json. Readers can enumerate the latest dated report. No repo source branches or runtime state are modified.

Workers are reported as NOT_OBSERVED if no message or commit evidence exists, not assumed idle or crashed. CI success is recorded as evidence only, not acceptance. A ChatGPT scheduled Production Director can read these files at its next scheduled run; GitHub Actions itself cannot wake an ordinary inactive chat or send a ChatGPT message.

Existing Studio hourly reconciliation at :03 remains authoritative for receipts and leased jobs. This GitHub Action serializes writes with the comms broker through the studio-comms-writer concurrency group. It is a deterministic non-spending step.

Remaining: merge and verify in real scheduling, register actual workers and approved jobs, audit time S00 task configuration, configure source audit keys, prove real cross-repo CI and end-to-end ACK. Preserve signed/encrypted Oversight cases in a distinct trust domain.
