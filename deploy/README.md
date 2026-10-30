# R5 deployment handoff (M owned)

The service has no AI worker daemon or model inference API. Configure **existing** GitHub repo/ref, permitted project channels and credential bindings with WG-G. Do not create workgroup branches during deployment.

Runtime construction order is WG-C APSW transaction state, WG-G authenticated GitHub readback/effect reconciler, WG-I cursor and relay observation recovery, WG-D preparation scheduling, WG-J authenticated FastAPI. `studio.app.lifecycle.StudioRuntime` runs all recovery hooks before starting any of these services, and shuts down in reverse order.

**Current M-only delivery:** `pip install -e '.[dev]'` and `python scripts/verify_r5_contracts.py /path/to/R5-packet` are executable. `python scripts/stage_handoff.py --help` is executable. A full network service cannot be started without the A-L source and real per-project configuration. Do not substitute a fake endpoint or browser runtime to make health checks pass.

Operational backups and restores are initiated through C's verified backup set and M's `RecoveryCoordinator`, with uncertain effects reconciled before restore and project CAS validated. External publication and readback are always mediated by WG-G and the source/QA/release gates in `studio.app`.

Required Tier C pilot: actual manual ChatGPT session sends a GitHub relay HELLO; authenticated G observes the remote ref and bytes; I projects the exact receipt; E/L verifies a real submission; M checks canonical integration; G verifies CI and release bytes. Record exact SHA/repo/project and label unavailable checks `NOT_RUN`.

`deploy/Dockerfile` and `deploy/compose.yaml` build the Python service using a **trusted configured composition factory** (`STUDIO_COMPOSITION_FACTORY=module:function`). The CLI fails closed with exit code 78 if the factory is unavailable; it never silently starts incomplete fake services. This production container does not replace WG-H's .NET host or WG-K's TypeScript cockpit, which have separate owners and must be assembled in the final deployment. `docker compose` and clean installation are not yet verified here.
