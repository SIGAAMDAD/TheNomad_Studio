# One-wave cleanup disposition ledger

Baseline Git tree `1644abf20ba78e9183d6d13e9761f77890ac4421`,
PR #36 `c9e5be46370f33a3c2e0b6ebddfc89132c7be68b`.

| Finding | Source disposition | Preserved authority / check |
|---|---|---|
| F01 | Deleted old Studio intake skill and descriptor | Authenticated R5R `submitFeature`, actual owner gate |
| F02 | Deleted obsolete S08 job example | Frozen operator OpenAPI remains |
| F03–F04 | Deleted scheduler policy and disconnected scripts | Trusted project source binding checks before APSW promotion |
| F05 | Replaced JSON source routes with owner-defined R5R source policy | Five exact repo/ref pairs and pinned commit SHA |
| F06 | Updated V4 manuals, schema/examples, validator and both standalone skill mirrors | 174 roles, 12 departments, browser shift identity |
| F07 | Removed time-triggered Apps Script source | External installed trigger still NOT_VERIFIED |
| F08 | RETAINED_PENDING_EXTERNAL_DEPENDENCY: engineering packet template/helper/test | Three source repos checked; two workflow directories inaccessible; do not delete until verified |
| F09 | Removed historic WG-C/G/H/K sidecars | Git commit object retains old bytes |
| F10 | Removed old WG-L output evidence/logs | Runnable `qa` package retained |
| F11 | Kept required mirrors; introduced immutable read-only mirror validator | 526 content-addressed resources and frozen contract mirrors |
| F12 | Updated maintenance source baseline; count generated from exact Git tree at commit | No false merged-main claim |
| F13 | Updated Architecture Enforcer and source-repository governance guidance | Real R5R source policy and owner decisions |
| F14 | Moved required 92-case pre-pilot matrix to `tests/prepilot/fixtures/`; retained runnable `tests/prepilot` | Current tests and CI are authoritative |

External installed skills/triggers and exact SHA independent review require
separate observed receipts. Nothing here claims actual browser worker F-20 PASS.
