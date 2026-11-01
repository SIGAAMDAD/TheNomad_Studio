# Studio runtime evidence branch

This branch retains historical operational records from the retired V1 Studio
controller. It is **not** the current job scheduler, state database, source
integration target or authority for production assignments.

The V1 ten-slot scheduler, its four GitHub Actions workflow definitions,
JSON job controller and periodic reconciliation were retired from active
source, and the historical branch workflows were removed on October 31, 2026.
The old event and evidence records remain available at their exact Git SHAs.

## Current authority

- Reviewed implementation and operator contracts: [main](https://github.com/SIGAAMDAD/TheNomad_Studio/blob/main/README.md)
- Durable project, worker, shift, command and effect state: authenticated
  Studio APSW coordinator
- GitHub communications: configured project-bound `studio/comms` channel
- Source integrations: each approved source repository at its pinned revision

Do not run the historical `scripts/studio.py` controller or activate V1 slot
assignments. Preserve existing records and avoid branch resets or force-pushes.
The current setup procedure lives in
[docs/SETUP.md](https://github.com/SIGAAMDAD/TheNomad_Studio/blob/main/docs/SETUP.md).
