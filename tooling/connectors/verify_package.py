"""Offline package byte pin verifier. No provider credentials, inference or execution.

Usage: PYTHONPATH=src python tooling/connectors/verify_package.py PROJECT_ID ROOT MANIFEST_SHA256
Manifest file must be ROOT/manifest.json. Exit 0 = bytes pinned (NOT tool smoke success).
"""
from __future__ import annotations

import sys
from pathlib import Path
from studio.integrations import PackageVerifier, ProjectBinding


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    _, project_id, path, expected = argv
    root = Path(path)
    try:
        result = PackageVerifier(ProjectBinding(project_id, "locally-verified"))\
            .verify(project_id=project_id, root=root,
                    manifest_bytes=(root / "manifest.json").read_bytes(), expected_digest=expected)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"BYTE_PIN_PASS package_id={result['package_id']} version={result['package_version']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
