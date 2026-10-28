"""Git-tree-derived path/mode validation, never a string startswith ACL."""
from __future__ import annotations

import re
from .interfaces import GitEntry

PATH = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?:/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)*\Z", re.ASCII)
# Explicitly restricted regardless of grant; the shared default is code-only.
RESTRICTED_ROOTS = frozenset({".git", ".github", "infra", "secrets", ".env"})
RESTRICTED_NAMES = frozenset({"id_rsa", "id_ed25519", "credentials.json", "private_key.pem", "secret.key",
                              ".env", "secrets", "credentials", ".ssh", "keys"})
RESTRICTED_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".kdbx")


def validate_paths(changed: tuple[str, ...] | list[str], prefixes: list[str],
                   original: tuple[GitEntry, ...], proposed: tuple[GitEntry, ...]) -> str | None:
    if not changed or len(set(changed)) != len(changed):
        return "INVALID_PATH"
    # Enforce canonical casing at *each directory level* across both trees.
    # Otherwise Foo/new.txt could coexist with foo/existing.txt on Linux
    # and silently alias on a case-insensitive checkout.
    by_fold: dict[str, str] = {}
    for e in (*original, *proposed):
        if not isinstance(e.path, str):
            return "INVALID_PATH"
        parts = e.path.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            key = prefix.casefold()
            if key in by_fold and by_fold[key] != prefix:
                return "INVALID_PATH"
            by_fold[key] = prefix
    old = {entry.path: entry for entry in original}
    new = {entry.path: entry for entry in proposed}
    all_paths = set(old) | set(new)
    for path in changed:
        if not isinstance(path, str):
            return "INVALID_PATH"
        parts = path.split("/")
        if (parts[0].lower() in RESTRICTED_ROOTS or
                any(p.lower() in RESTRICTED_NAMES for p in parts) or
                parts[-1].lower().endswith(RESTRICTED_SUFFIXES)):
            return "PROTECTED_PATH"
        if not PATH.fullmatch(path):
            return "INVALID_PATH"
        if any(p.startswith(".") for p in parts):
            return "INVALID_PATH"
        if any(path.casefold() == p.casefold() and path != p for p in all_paths):
            return "INVALID_PATH"
        if not any(path.startswith(prefix) and len(path) > len(prefix) for prefix in prefixes):
            return "INVALID_SCOPE"
        # Tree must contain at least one old/new side; no fabricated changed path.
        if path not in old and path not in new:
            return "INVALID_PATH"
        for e in (old.get(path), new.get(path)):
            if e and e.mode not in ("100644", "100755"):
                return "INVALID_PATH"  # symlink/submodule/tree modes never admitted
        # Reject even indirect access through an existing symlink ancestor.
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if any(e and e.mode in ("120000", "160000") for e in (old.get(prefix), new.get(prefix))):
                return "INVALID_PATH"
    return None
