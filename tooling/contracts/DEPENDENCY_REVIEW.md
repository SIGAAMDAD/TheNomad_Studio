# WG-M dependency decisions and test status

| Candidate | Decision in WG-M | Integration owner | Verification |
|---|---|---|---|
| Python stdlib (`zipfile`, `hashlib`, `fcntl` / `msvcrt`, `tempfile`) | Used for ownership-safe intake, atomic local tree mirror and lifecycle | M | Local tests executed |
| APSW | Coordinator integration extra, sole authoritative SQL transaction adapter | C | NOT_RUN: not installed in this worker environment |
| FastAPI / Uvicorn | Coordinator integration extra; HTTP/auth route owner | J | API route assembly NOT_RUN: no WG-J implementation delivered |
| pytest | Test runner for local M tests | M | Executed; environment version recorded in test log |
| .NET 10 runtime | Not installed by this Python package; execution host belongs to H | H | NOT_RUN |
| React/Vite/TanStack Query | Not installed by M; cockpit belongs to K | K | NOT_RUN |
| GitHub authenticated connector | No bundled GitHub auth secret; G owns API integration | G | NOT_RUN, readback adapters fail closed without real provider |

Python core has **zero external runtime dependencies**, permitting isolated M source testing. The optional `coordinator` extra describes expected A-L assembly packages but versions are **minimum compatibility ranges, not an audited lockfile**. Versions, package file hashes, licenses, vulnerability review, exact locks and a full clean install remain required for final integrated release; M does not invent dependency versions or claim an unexecuted clean build.
