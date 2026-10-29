#!/usr/bin/env python3
"""CI static forbidden patterns / inadvertent credential scan, not a substitute for scanning history."""
import argparse
from pathlib import Path
import re
import sys

PATTERNS = {
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
    'github-personal-token': re.compile(rb'gh[pousr]_[A-Za-z0-9_]{20,}'),
    'github-fine-token': re.compile(rb'github_pat_[A-Za-z0-9_]{20,}'),
    'google-api-key': re.compile(rb'AIza[0-9A-Za-z_-]{35}'),
    'aws-access-key': re.compile(rb'AKIA[0-9A-Z]{16}'),
}
INSECURE_BIND = re.compile(rb'(?i)(?:gateway_host|--bind|listen)\s*[=: ]+\s*["\x27]?0\.0\.0\.0')


def scan(root: Path):
    problems = []
    for path in root.rglob('*'):
        if not path.is_file() or path.is_symlink() or '.git' in path.parts or '__pycache__' in path.parts or path.suffix in ('.pyc', '.zip', '.sqlite3'):
            continue
        if path.stat().st_size > 2_000_000:
            continue
        data = path.read_bytes()
        for label, pat in PATTERNS.items():
            if pat.search(data):
                problems.append((str(path.relative_to(root)), label))
        if path.name.endswith(('.conf', '.service')) and INSECURE_BIND.search(data):
            problems.append((str(path.relative_to(root)), 'public-gateway-bind'))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args(argv)
    problems = scan(args.root)
    for name, rule in problems:
        print(name + ': ' + rule)
    if problems:
        return 1
    print('No matching credential literals or public gateway binds in scanned files')
    return 0


if __name__ == '__main__':
    sys.exit(main())
