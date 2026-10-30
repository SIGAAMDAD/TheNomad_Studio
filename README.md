# The Nomad Studio — R9 local-authoritative implementation candidate

This tree is the **local source authority** for the first Studio release. The prior
remote `main` tree is not a base for preserving old runtime behavior. There are
no legacy facade or database migration obligations.

Current scope: the ten CAB-A..J source submissions plus direct cross-cabal seam
repairs, the P2 exact-schema SQLite authority, the composed 22-port R9 runtime,
and an expanded deterministic integration, fault and raw-unit campaign. Four
separate original P1 test/release submissions were not included in this source.
A passed local test campaign is **not** QA_READY, authenticated native host
activation, provider acceptance, exact-commit CI success or deployment.

## Unit and fault simulation

```bash
python -m pip install -e '.[dev]'
python infra/greenfield/ci_gate.py .
python infra/greenfield/r9_raw_unit_gate.py . --mode raw-unit --out r9-unit-receipt.json --log r9-unit-tests.log
python infra/greenfield/r9_raw_unit_gate.py . --mode full --out r9-integrated-receipt.json --log r9-integrated-tests.log
```

The first mode exercises the original raw-unit and fault-simulation campaign,
excluding integration tests. The second mode also runs real R9 P2 root tests,
negative authorization/SQLite ownership tests, restart recovery and durable
outbox readback checks. Neither mode supplies native external provider proof.

Build and cold-test the installed package independently of the source tree:

```bash
python -m pip wheel . --no-deps -w dist
python infra/greenfield/r9_wheel_smoke.py --wheel dist/nomad_studio-1.9.0-py3-none-any.whl
```

The systemd entrypoint uses `--config /etc/nomad-studio/r9-runtime.json`.
For operational startup, the **trusted operator** installs a provider-specific Python
module and sets `NOMAD_STUDIO_RUNTIME_FACTORY=module:function` in the private
`/etc/nomad-studio/service.env` (or uses `--runtime-factory` directly). The
factory is called with keyword arguments `sqlite_path` and `cas_path`, must
construct the existing `StudioRuntime` using `open_studio(..., bindings=...)`,
start the existing mTLS gateway with `runtime.start_gateway(...)`, and return
that runtime. It is an executable native integration provided by the operator,
not a config-supplied mock, dynamic provider discovery, or legacy shim.
The process verifies that the resulting runtime owns exactly the configured
storage paths and has genuinely passed its existing provider/host readiness
checks before entering the outbox execution loop. Absent or failed native
readiness exits `2` without accepting owner commands. The checked-in source
contains no native external provider implementation or its credentials, so it
cannot activate a live Studio merely by setting a factory name.
The systemd service blocks restart attempts for exit code 2 (native providers
not configured). The previous periodic health timer was removed because it
would continually fail without live native providers. `--doctor` is a
**read-only SQLite diagnostic**, not an active-process/native-provider proof.
Start from `infra/greenfield/r9-runtime.example.json`. The config only names
absolute private storage paths; provider credentials and host attestations
must be injected through trusted native integration, not accepted from a user
command or inferred from the presence of a file. While native providers and
TLS ingress are absent, the runtime responds `NOT_READY` and exits without
exposing a public listener.

`QA/R9_LOCAL_VERIFICATION.md` identifies the executed tests and remaining gates.
Production release requires independent QA, exact-commit PR CI, authenticated
provider and host proof, and operational deployment verification.
