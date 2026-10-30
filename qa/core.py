"""Evidence primitives shared by local, integrated and manually witnessed QA."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import os
import tempfile

STATES = frozenset({'PASS', 'FAIL', 'NOT_RUN'})
TIERS = frozenset({'A', 'B', 'C'})


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')


def read_json(path: Path) -> Any:
    def no_duplicate(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError(f'duplicate JSON key: {k}')
            result[k] = v
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=no_duplicate)


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value or value.startswith('/'):
        raise ValueError(f'unsafe path: {value!r}')
    components = value.split('/')
    if any(x in ('', '.', '..') for x in components) or ':' in components[0]:
        raise ValueError(f'unsafe path: {value!r}')
    return value


def source_files(root: Path) -> dict[str, str]:
    """Hash actual regular files; refuse links (they could escape the source root)."""
    if not root.is_dir():
        raise FileNotFoundError(root)
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'symlink in candidate: {path}')
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(p in {'.git', '__pycache__', '.pytest_cache', '.venv', 'node_modules', 'bin', 'obj'} for p in path.relative_to(root).parts):
            continue
        files[safe_relative(rel)] = digest(path.read_bytes())
    return files


def tree_digest(files: dict[str, str]) -> str:
    """Content identity of full candidate tree, independent of timestamps/machine."""
    return digest(json_bytes(files))


def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.qa-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(json_bytes(obj))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@dataclass(frozen=True)
class Result:
    test_id: str
    tier: str
    status: str
    detail: str
    evidence: dict[str, Any]

    def __post_init__(self):
        if self.status not in STATES or self.tier not in TIERS:
            raise ValueError('unsupported test status/tier')
        if self.status == 'PASS' and not self.evidence:
            raise ValueError('PASS requires observed evidence')

    def record(self) -> dict[str, Any]:
        return asdict(self)


def result(test_id: str, tier: str, status: str, detail: str, **evidence: Any) -> Result:
    return Result(test_id, tier, status, detail, evidence)
