# Legacy decommission verification

Source baseline: PR #36 `c9e5be46370f33a3c2e0b6ebddfc89132c7be68b`.
The obsolete timed Apps Script code and old Studio intake skill are absent
from the cleaned implementation source. The engineering-review template and
packet builder are retained as an isolated external compatibility dependency
until remaining source-repository consumers can be verified.

Source-repository workflow census (GitHub authenticated repository listing):
- `SIGAAMDAD/Terrain3D`: eight workflow files, no `engineering-review.yml`.
- `SIGAAMDAD/NomadFramework`: one workflow file, no `engineering-review.yml`.
- `SIGAAMDAD/TheNomad_GOAP`: one workflow file, no `engineering-review.yml`.
- `SIGAAMDAD/The_Nomad`: workflow directory not accessible from the connection.
- `SIGAAMDAD/NomadMain`: workflow directory not accessible from the connection.

Separate external installations are NOT_VERIFIED: an Apps Script timed trigger
may remain configured outside this repository; a previously installed copy
of the old ChatGPT intake skill or ChatGPT scheduled task may remain.
An absent source file is **not** a verified external deactivation. The human
owner or an authorized connected administration workflow must inspect and
retire those instances separately before claiming full external decommission.
The private/unavailable repository workflow dependency has not been
certified absent, so the existing evidence packet helper remains at its original
path. No source repository or external Apps Script installation was modified by
this cleanup. Current Studio source CI remains the verified R5R validation workflow.
