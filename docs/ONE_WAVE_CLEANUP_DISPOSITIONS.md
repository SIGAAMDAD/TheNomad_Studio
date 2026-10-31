# One-wave cleanup - merged disposition ledger

Source parent: PR #36 head `c9e5be46370f33a3c2e0b6ebddfc89132c7be68b` (Git tree `1644abf20ba78e9183d6d13e9761f77890ac4421`). Cleanup PR #37 head `51991c58fde74022841153e8032d430eeb475dd8` was merged as `7d18db7483c69c89cad92183dd598c133f184b41`. Merged source tree: `9eccfcb5e0d822f4777741b8baab7180b1fd31db`.

| Finding | Merged disposition | Remaining dependency / validation |
| --- | --- | --- |
| F01 | Removed obsolete `nomad-studio-intake` skill and descriptor | Use authenticated R5R intake (`submitFeature`) and owner grants |
| F02 | Removed old slot-based job example | Frozen 72-operation API unchanged |
| F03-F04 | Removed disconnected scheduler policy scripts | Source and architecture authority enforced through the trusted R5R boundary |
| F05 | Replaced JSON source routes with `src/studio/app/source_policy.py` | Five approved repo/ref pairs, project binding and SHA validation |
| F06 | Updated V4 department/worker references and installed skill mirrors | 174 roles, 12 departments, actual worker/shift identities |
| F07 | Removed timed Apps Script source | Installed external trigger deactivation **not verified** |
| F08 | Retained engineering packet template, helper and tests | Unavailable private source-repo workflow consumers require direct verification |
| F09 | Removed old WG-C/G/H/K handoff sidecars | Available in historical Git revision |
| F10 | Removed old WG-L output logs | Runnable independent QA harness remains |
| F11 | Retained required contract and catalog distribution mirrors; added exact checks | 526 content-addressed resource entries preserved |
| F12 | Corrected repository cleanup inventories | Recompute counts after subsequent commits |
| F13 | Updated Architecture Enforcer and source-repository routing guidance | Owner-controlled source revision and proposal gates |
| F14 | Preserved 92-case pre-pilot test matrix under `tests/prepilot/fixtures/` | Real provider and F-20 acceptance remain separate |

CI at PR #37 implementation head reported nine successful check runs (Python, TypeScript, .NET, source provenance and V4 skills). This does not establish that a live production service or browser workforce is operating. See [Owner acceptance](OWNER_SETUP.md) and [Decommission status](LEGACY_DECOMMISSION_STATUS.md).
