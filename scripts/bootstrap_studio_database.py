#!/usr/bin/env python3
"""Create the frozen APSW Studio database once; never overwrite existing state."""
from __future__ import annotations

import argparse
from pathlib import Path

from studio.storage.workforce import WorkforceStorage


def bootstrap_database(path: Path) -> None:
    if str(path) in ('', ':memory:') or path.exists():
        raise ValueError('new on-disk database path required; existing state is protected')
    path.parent.mkdir(parents=True, exist_ok=True)
    store = WorkforceStorage.open(path, fresh=True)
    store.close()


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database', type=Path, required=True)
    args = cli.parse_args()
    bootstrap_database(args.database)
    print(f'Initialized Studio APSW database: {args.database}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
