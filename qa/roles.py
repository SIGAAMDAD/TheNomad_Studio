"""Byte-proven role/skill readiness; missing 174-role resources are never filled in."""
from __future__ import annotations
from pathlib import Path
from .core import digest, read_json, safe_relative


class RoleError(ValueError):
    pass


def verify_roles(root: Path, manifest: Path, target: int = 174) -> dict:
    """Input: manifest with `roles`: id/skill_files(hash)/smoke(log,hash,exit)."""
    if not manifest.is_file() or not root.is_dir():
        raise FileNotFoundError('real role inventory and source are required')
    rows = read_json(manifest).get('roles')
    if not isinstance(rows, list):
        raise RoleError('roles must be a list')
    seen = set()
    defects = []
    verified = 0
    for role in rows:
        rid = role.get('role_id')
        if not isinstance(rid, str) or not rid or rid in seen:
            defects.append(f'invalid/duplicate role id: {rid}')
            continue
        seen.add(rid)
        files = role.get('skill_files', {})
        smoke = role.get('smoke', {})
        if not isinstance(files, dict) or not files:
            defects.append(f'{rid}: missing skill resource bytes'); continue
        try:
            for path, sha in files.items():
                src = root / safe_relative(path)
                if src.is_symlink() or not src.is_file() or digest(src.read_bytes()) != sha:
                    raise RoleError(f'{rid}: skill file missing/tampered: {path}')
            if smoke.get('exit_code') != 0 or not isinstance(smoke.get('argv'), list) or not smoke['argv']:
                raise RoleError(f'{rid}: smoke command not evidenced')
            log = root / safe_relative(smoke['log_file'])
            if log.is_symlink() or not log.is_file() or digest(log.read_bytes()) != smoke.get('log_sha256'):
                raise RoleError(f'{rid}: smoke output missing/tampered')
            verified += 1
        except (RoleError, ValueError, KeyError) as exc:
            defects.append(str(exc))
    if len(seen) > target:
        defects.append(f'inventory exceeds target {target}')
    return {'target': target, 'present_ids': len(seen), 'ready': verified,
            'missing_count': max(0, target - len(seen)),
            'defects': defects, 'ready_ids': sorted(seen) if verified == len(seen) else []}
