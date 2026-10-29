#!/usr/bin/env python3
"""Run the complete Studio V1 test campaign, including AG–AV submissions."""
from pathlib import Path
import os
import re
import subprocess
import sys

from ci_gate import check


def run(root: Path) -> int:
    issues = check(root)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 2
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join((
        str(root / 'src'), str(root / 'tests'), str(root),
        str(root / 'tests' / 'trust'),
        str(root / 'tests' / 'creative_authority' / 'impact'),
        str(root / 'tests' / 'oversight_development'),
        env.get('PYTHONPATH', ''),
    ))
    cmd = [sys.executable, '-m', 'pytest', '-q', '--disable-warnings', str(root / 'tests')]
    result = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=600)
    print(result.stdout, end='')
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0 or not re.search(r'\b\d+ passed\b', result.stdout):
        print('FAIL: Studio V1 full-suite tests failed or were not collected', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(run(Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()))
