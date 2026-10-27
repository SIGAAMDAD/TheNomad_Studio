# Legacy GOAP controller cleanup — V1 scope

GOAP source lives in SIGAAMDAD/TheNomad_GOAP and is not part of the Studio controller's application code. Current Studio runtime references FEATURE-6 and FEATURE-7; FEATURE-6-G00-01 has a blocked source-freeze gate, while separate Source Baseline PR #16 was created against an older Studio main. These records are not evidence of a running worker.

V1 cleanup retires the stale **draft control-plane source-baseline PR** once the updated source routing is verified; do not delete/overwrite the actual GOAP repository, its CI baseline, the immutable event/receipt history, or accepted architecture. Keep FEATURE-6 explicitly BLOCKED pending source authorization and treat FEATURE-7 as a separate unapproved content request unless owner formally cancels them. No source-write gate or feature approval is silently granted by a version reset.

The legacy previous-version hierarchy/prompt files remain as archived compatibility references for existing live jobs. Only studio/v1/prompt index and the updated role skills are authoritative for newly launched workers.
