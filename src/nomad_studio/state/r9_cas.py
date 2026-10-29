"""Local immutable content-addressed bytes for R9, not provider authorization."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import secrets
import stat
from typing import Any, Mapping

_SHA = re.compile(r'^[0-9a-f]{64}$')


class FileCAS:
    def __init__(self, directory: str | Path):
        self.root = Path(directory).absolute()
        if self.root.is_symlink():
            raise ValueError('CAS directory cannot be a symlink')
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.name == 'posix' and stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise ValueError('CAS directory must be private')

    def put(self, data: bytes, digest: str) -> str:
        if type(data) is not bytes or not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise ValueError('CAS requires SHA-256 of immutable bytes')
        if sha256(data).hexdigest() != digest:
            raise ValueError('CAS digest differs from bytes')
        dest_dir = self.root / digest[:2]
        dest_dir.mkdir(mode=0o700, exist_ok=True)
        if dest_dir.is_symlink() or not dest_dir.is_dir():
            raise ValueError('CAS shard directory must be real')
        if os.name == 'posix' and stat.S_IMODE(dest_dir.stat().st_mode) & 0o077:
            raise ValueError('CAS shard directory must be private')
        dest = dest_dir / digest
        if dest.exists():
            if self.get(digest) != data:
                raise ValueError('CAS collision or corruption')
            return digest
        temp = dest_dir / ('.stage-' + secrets.token_hex(16))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, 'O_NOFOLLOW'):
            flags |= os.O_NOFOLLOW
        fd = os.open(temp, flags, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Multiple processes writing an identical SHA may race, but the bytes
            # are content-addressed and always verified after placement.
            os.replace(temp, dest)
            dirfd = os.open(dest_dir, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            temp.unlink(missing_ok=True)
        return digest

    def get(self, ref: str) -> bytes:
        if not isinstance(ref, str) or not _SHA.fullmatch(ref):
            raise KeyError('invalid immutable byte reference')
        path = self.root / ref[:2] / ref
        if path.is_symlink() or not path.is_file():
            raise KeyError('CAS bytes unavailable')
        data = path.read_bytes()
        if sha256(data).hexdigest() != ref:
            raise ValueError('CAS stored bytes corrupted')
        return data


class RecordCAS:
    """CAB-F record access to the same immutable file-backed byte store."""
    def __init__(self, cas: FileCAS):
        self.cas = cas

    def put(self, record: Mapping[str, Any]) -> str:
        if not isinstance(record, Mapping):
            raise TypeError('Council requires an immutable record')
        value = json.dumps(record, ensure_ascii=True, sort_keys=True,
                           allow_nan=False, separators=(',', ':')).encode('utf-8')
        digest = sha256(value).hexdigest()
        return self.cas.put(value, digest)

    def get(self, ref: str) -> dict[str, Any]:
        data = self.cas.get(ref)
        obj = json.loads(data)
        if not isinstance(obj, dict):
            raise ValueError('invalid council record')
        return obj
