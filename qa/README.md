# Independent QA and pre-pilot evidence harness

The executable `qa` package is retained for optional local acceptance against
an explicit frozen R5 packet and user-supplied source/role evidence. It does
not make live-provider claims from offline reference tests.

```sh
python -m unittest discover -s tests/unit/l -p 'test_*.py' -v
python -m pytest -q tests/unit tests/integration tests/acceptance
python tests/prepilot/run_pre_pilot.py --help
```

For an offline packet audit, supply your actual packet path and an output
folder to `python -m qa.runner --packet /path/to/packet --out /path/to/report`.
The generated `results.json`, logs and witness records are written to the
output folder; do not commit old immutable WG-L output as current evidence.
A missing runtime, package or remote observation remains `NOT_RUN`.
See `docs/HISTORICAL_SOURCE_EVIDENCE.md` for historical handoff references.
