#!/usr/bin/env python3
"""Operator-run local preflight. Output is redacted; no secret bytes read."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from nomad_studio.deployment.config import DeploymentConfig, ConfigurationError
from nomad_studio.deployment.health import check_host


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Greenfield host preflight (never release acceptance)')
    parser.add_argument('--config', required=True, help='JSON containing file PATHS, not credentials')
    args = parser.parse_args(argv)
    try:
        config = DeploymentConfig.from_mapping(json.loads(Path(args.config).read_text(encoding='utf-8')))
        def systemd_alive():
            result = subprocess.run(['systemctl', 'is-active', '--quiet', 'nomad-studio.service'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
            return result.returncode == 0
        checks = check_host(config, service_alive=systemd_alive)
    except (ConfigurationError, ValueError, OSError) as exc:
        # Invalid paths or JSON must not be printed; they might contain tokens.
        print(json.dumps({'status': 'BLOCKED', 'code': getattr(exc, 'code', 'INVALID_SCHEMA')}))
        return 2
    bad = any(c.state == 'BLOCKED' for c in checks)
    missing = bool(config.missing_settings()) or any(c.state == 'NOT_RUN' for c in checks)
    print(json.dumps({'status': 'BLOCKED' if bad else 'NOT_RUN' if missing else 'UNKNOWN',
                      'code': 'HOST_CHECK_FAILED' if bad else 'NOT_CONFIGURED' if missing else 'EXTERNAL_VERIFICATION_REQUIRED',
                      'checks': [vars_check(c) for c in checks],
                      'release_eligible': False}, sort_keys=True))
    return 2 if bad else 3 if missing else 0


def vars_check(c):
    return {'name': c.name, 'state': c.state, 'code': c.code}


if __name__ == '__main__':
    sys.exit(run())
