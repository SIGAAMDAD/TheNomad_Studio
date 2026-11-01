#!/usr/bin/env python3
"""Create the frozen APSW Studio database once; never overwrite existing state."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from studio.storage.workforce import WorkforceStorage


def bootstrap_database(path: Path) -> None:
    if str(path) in ('', ':memory:'):
        raise ValueError('new on-disk database path required')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Reserve the filename atomically; two concurrent bootstrap attempts
        # must never race to initialize the same on-disk Studio database.
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('existing state is protected') from exc
    else:
        os.close(handle)
    # A failed initialization retains the reserved file for explicit inspection
    # instead of silently retrying potentially incomplete database creation.
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
