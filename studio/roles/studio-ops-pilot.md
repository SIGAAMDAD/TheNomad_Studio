# Studio operational pilot - restricted role

This is a one-time infrastructure verification role, not one of the 174 production specialist manuals. Only OPS-BOOT-001 and OPS-QA-001 may use it.

## S01 - OPS-BOOT-001

1. Read the assignment in studio/runtime/studio/slots.json and jobs.json; verify your job, slot, and epoch exactly.
2. Read only TheNomad_Studio control-plane files. Commit a concise actual handshake report at studio/evidence/OPS-BOOT-001.md in studio/runtime. Include the observed source commit, assignment epoch, the current non-paid dispatch switches, and limitations. Do not modify game code, canon, job/slot/config files, or API spending settings.
3. Read back the actual report commit SHA and create an immutable SUBMITTED receipt under studio/receipts/OPS-BOOT-001/<ATTEMPT_ID>.json with job_id, slot_id, assignment_epoch, attempt_id, outcome and artifact_ref pinned to the report commit.
4. If permissions or state checks fail, report BLOCKED with a precise reason. Never invent completion evidence.

## S08 - OPS-QA-001

1. Run only when assigned the independent QA job after its subject OPS-BOOT-001 is SUBMITTED.
2. Read the exact immutable artifact_ref from the subject job, verify real GitHub content and absence of game-source changes. Do not review your own work.
3. Commit studio/evidence/OPS-QA-001.md in studio/runtime with PASS/REWORK verdict, subject commit, concrete findings, and what was actually inspected.
4. Create a separate immutable SUBMITTED receipt for OPS-QA-001 targeting the real QA report commit. The Director independently examines this report and may validate the parent subject; the QA worker never self-promotes to VERIFIED.
