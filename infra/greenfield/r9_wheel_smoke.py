#!/usr/bin/env python3
"""Verify the assembled R9 wheel from a cold directory without source imports.

Expected outcome without native host bindings: full local composition and frozen
state load, plus explicit NOT_READY, never a fabricated production-ready state.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel', type=Path, required=True)
    args = parser.parse_args(argv)
    wheel = args.wheel.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='nomad-r9-wheel-') as temp:
        root = Path(temp)
        install = root/'installed'
        package = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index',
            '--no-deps', '--target', str(install), str(wheel)], cwd=root,
            capture_output=True, text=True, timeout=90, check=False)
        if package.returncode:
            print(json.dumps({'result':'FAIL','step':'COLD_INSTALL','code':package.returncode}))
            return 1
        config = root/'config.json'
        config.write_text(json.dumps({'sqlite_path': str(root/'private'/'studio.sqlite'),
                                      'cas_path': str(root/'private'/'cas')}), encoding='utf-8')
        env = dict(os.environ)
        env['PYTHONPATH'] = str(install)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        proc = subprocess.run([sys.executable, '-m', 'nomad_studio.app',
                               '--doctor', '--config', str(config)], cwd=root,
                              capture_output=True, text=True, env=env,
                              timeout=40, check=False)
        try:
            status = json.loads(proc.stdout)
        except ValueError:
            status = {}
        if not (proc.returncode == 2 and status.get('database') == 'R9_FRESH_SCHEMA_VERIFIED'
                and status.get('registered_port_count') == 22
                and status.get('readiness') == 'NOT_READY'):
            print(json.dumps({'result':'FAIL','step':'COLD_ENTRYPOINT','returncode':proc.returncode,
                              'status': status, 'stderr_excerpt':proc.stderr[:500]}))
            return 1
        check = subprocess.run([sys.executable, '-c',
            'from importlib.metadata import distribution; from nomad_studio.app import open_studio; '
            'from importlib.resources import files; '
            'assert files("nomad_studio.state").joinpath("r9_initial_schema.sql").is_file(); '
            'assert distribution("nomad-studio").version == "1.9.0"'],
            cwd=root, env=env, capture_output=True, text=True, timeout=30, check=False)
        if check.returncode:
            print(json.dumps({'result':'FAIL','step':'PACKAGE_DATA','stderr_excerpt':check.stderr[:500]}))
            return 1
    print(json.dumps({'result':'PASS_LOCAL_WHEEL_ONLY','port_count':22,'native_acceptance':'NOT_VERIFIED'}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
