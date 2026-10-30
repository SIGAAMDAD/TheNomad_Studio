# The Nomad Studio R5 — WG-M integration implementation

This is the **Workgroup M changed-files-only source** for the greenfield, manually initiated ChatGPT browser/GitHub relay Studio. It is not a release of the entire Studio. The authoritative R5 packet stays read-only.

## Code and behavior

- `studio.app.intake`: 13 exclusive ownership domains, frozen R5 plan digest, changed-file ZIP SHA-256, path traversal/symlink/case collision/size checks and exact baseline identity.
- `studio.app.integration`: one per-project canonical local tree, content-addressed snapshots, multi-worker nonoverlapping integration, stale base rejection, durable staging, locked CAS pointer and injected independent QA / authenticated source readback.
- `studio.app.verification`: exact source and independent review checks; no claims based on a chat-authored status or tests reported in handoff metadata.
- `studio.app.projects`: project create/list/get/configure/preflight through C's authoritative scoped transaction interface.
- `studio.app.releases`: source/CANON/CI/artifact gating, prepared release records, exact owner effect approval, observe-before-publish and unknown outcome handling; recovery intent with backup verification and project CAS.
- `studio.app.lifecycle`: deterministic recover-before-start and reverse-order shutdown; never creates browser chats.
- `studio.app.composition`: explicit dispatch of M domain API operations without creating an alternate API owned by J.

**Boundary:** Integrating local source bytes does not prove GitHub commit, independently executed WG-L QA, C's APSW transaction, owner authorization or live deployment. Those adapters are injected from A/C/E/G/I/J/L when delivered. M cannot truthfully report Tier C success before they exist and actually run.

## Repeatable local checks

```sh
NOMAD_R5_PACKET=/path/to/extracted/R5-packet PYTHONPATH=src python -m pytest tests/unit/m tests/integration -q
PYTHONPATH=src python scripts/verify_r5_contracts.py /path/to/extracted/R5-packet
PYTHONPATH=src python scripts/stage_handoff.py --help
```

## Handoff metadata format

The ZIP itself contains only changed **owned** source paths. Outside the ZIP, provide a JSON object:

```json
{"group":"M","plan_sha256":"<64 hex>","base_revision_id":"<canonical tree revision>",
 "files":{"src/studio/app/example.py":{"before":null,"after":"<sha256>"}},
 "tests":[{"command":"python -m pytest ...", "exit_code":0, "tier":"B"}]}
```

`before` is the canonical path digest when replacing/removing, otherwise `null`. `after` is the new file digest or `null` for deletion. The canonical tree begins empty; obtain its revision id from `CanonicalTree.current_revision()` before staging. A stage does **not** promote: promotion requires exact remote source readback and independent QA for the candidate bytes.
