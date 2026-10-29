# The Nomad Studio — R9 local-authoritative implementation candidate

This tree is the **local source authority** for the first Studio release. The prior
remote `main` tree is not a base for preserving old runtime behavior. There are
no legacy facade or database migration obligations.

Current scope: the ten CAB-A..J source submissions plus direct cross-cabal seam
repairs, and a deterministic adversarial raw-unit acceptance campaign. The four
separate P1 test/release worker submissions and final P2 22-port application
composition are still outstanding. A passed unit campaign is **not** QA_READY,
verified ChatGPT host activation, provider acceptance, CI success or deployment.

## Unit and fault simulation

```bash
python -m pip install -e '.[dev]'
python infra/greenfield/ci_gate.py .
python infra/greenfield/r9_raw_unit_gate.py . --mode raw-unit --out r9-unit-receipt.json --log r9-unit-tests.log
python infra/greenfield/r9_raw_unit_gate.py . --mode full --out r9-integrated-receipt.json --log r9-integrated-tests.log
```

The first mode exercises the full source/contract/SQLite/fault-simulation unit
matrix excluding the obsolete root composition test. The second mode is a
mandatory full-suite gate, currently **RED** until P2 installs the R9 root
runtime and replaces obsolete V1 integration tests with R9 system tests.

`QA/R9_LOCAL_VERIFICATION.md` identifies the executed tests and remaining gates.
Production release requires independent QA, exact-commit PR CI, authenticated
provider and host proof, and operational deployment verification.
