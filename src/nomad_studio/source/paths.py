"""Closed, deliberately conservative Git path-ownership validation.

Write paths are ASCII-only to make Unicode spoofing unrepresentable. Existing
Unicode filenames can be read through tree metadata, but never authorized as
mutations by this policy version. This is a documented, fail-closed limitation.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Mapping, Sequence


class PathError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


_SEGMENT = re.compile(r"^[A-Za-z0-9_.@+()\- ]+$", re.ASCII)
_WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})
# Some common Unicode homoglyphs, in addition to NFKC / case folding.
_LOOKALIKE = str.maketrans({
    "а": "a", "А": "A", "е": "e", "Е": "E", "о": "o", "О": "O", "р": "p", "Р": "P",
    "с": "c", "С": "C", "у": "y", "У": "Y", "х": "x", "Х": "X", "і": "i", "І": "I",
    "ј": "j", "Ј": "J", "к": "k", "К": "K", "м": "m", "М": "M", "н": "h", "Н": "H",
    "т": "t", "Т": "T", "в": "b", "В": "B", "ѕ": "s", "Ѕ": "S", "ԁ": "d",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X", "ο": "o", "ρ": "p",
    "α": "a", "ι": "i", "ν": "v", "κ": "k", "τ": "t", "χ": "x", "ε": "e",
})


def skeleton(path: str) -> str:
    result = unicodedata.normalize("NFKD", path).translate(_LOOKALIKE)
    return "".join(c for c in result if not unicodedata.combining(c)).casefold()


def path_key(path: str) -> str:
    return skeleton(path)


def canonical(path: object) -> str:
    if not isinstance(path, str) or not path or len(path) > 1024:
        raise PathError("INVALID_PATH", "path must be a nonempty bounded string")
    if not path.isascii() or unicodedata.normalize("NFKC", path) != path:
        raise PathError("INVALID_PATH", "non-ASCII or compatibility paths cannot be written")
    if path.startswith("/") or "\\" in path or ":" in path or "\x00" in path:
        raise PathError("INVALID_PATH", "absolute, drive, NUL and backslash paths are forbidden")
    parts = path.split("/")
    if any(not part or part in (".", "..") or part[-1] in (".", " ") for part in parts):
        raise PathError("INVALID_PATH", "empty, relative or ambiguous segments are forbidden")
    for part in parts:
        if not _SEGMENT.fullmatch(part) or part.split(".")[0].upper() in _WINDOWS_RESERVED:
            raise PathError("INVALID_PATH", "unsafe path segment")
    if parts[0] == ".git":
        raise PathError("PROTECTED_PATH", "Git metadata is protected")
    return path


def pattern(value: object) -> str:
    if not isinstance(value, str):
        raise PathError("INVALID_PATH", "pattern must be a string")
    if value.endswith("/**"):
        base = canonical(value[:-3])
        return base + "/**"
    return canonical(value)


def matches(path: str, pattern_value: str) -> bool:
    if pattern_value.endswith("/**"):
        prefix = pattern_value[:-3]
        return path == prefix or path.startswith(prefix + "/")
    return path == pattern_value


def covered(path: str, patterns: Sequence[str]) -> bool:
    return any(matches(path, item) for item in patterns)


def change_paths(changes: object) -> tuple[tuple[str, ...], tuple[Mapping[str, str], ...]]:
    if not isinstance(changes, list) or len(changes) > 1024:
        raise PathError("INVALID_SCHEMA", "changes must be a bounded list")
    inspected: list[str] = []
    parsed: list[Mapping[str, str]] = []
    for raw in changes:
        if not isinstance(raw, dict):
            raise PathError("INVALID_SCHEMA", "change must be an object")
        action = raw.get("action")
        if action in ("add", "modify"):
            required = {"action", "path"}
            if set(raw) != required:
                raise PathError("INVALID_SCHEMA", "add/modify require path only")
            dest = canonical(raw["path"])
            inspected.append(dest)
            parsed.append({"action": action, "path": dest})
        elif action == "delete":
            if set(raw) != {"action", "source"}:
                raise PathError("INVALID_SCHEMA", "delete requires source only")
            source = canonical(raw["source"])
            inspected.append(source)
            parsed.append({"action": action, "source": source})
        elif action in ("rename", "copy"):
            if set(raw) != {"action", "source", "destination"}:
                raise PathError("INVALID_SCHEMA", "rename/copy require both endpoints")
            source, dest = canonical(raw["source"]), canonical(raw["destination"])
            if source == dest or path_key(source) == path_key(dest):
                raise PathError("INVALID_PATH", "identical or case-only rename/copy denied")
            inspected.extend((source, dest))
            parsed.append({"action": action, "source": source, "destination": dest})
        else:
            raise PathError("INVALID_SCHEMA", "unknown change action")
    if len(inspected) != len(set(inspected)):
        raise PathError("INVALID_PATH", "overlapping mutations in one request")
    return tuple(inspected), tuple(parsed)


def inspect_tree(changes: Sequence[Mapping[str, str]], tree_entries: Sequence[object],
                 owned: Sequence[str], protected: Sequence[str], resource_scope: Sequence[str]) -> None:
    """Validate both endpoints and full tree metadata, without reading source blobs."""
    tree: dict[str, object] = {}
    keys: dict[str, str] = {}
    for entry in tree_entries:
        name = getattr(entry, "path", None)
        kind = getattr(entry, "kind", None)
        if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name or "\x00" in name or any(s in ("", ".", "..") for s in name.split("/")):
            raise PathError("INVALID_PATH", "provider supplied an unsafe tree entry")
        if kind not in ("blob", "tree", "symlink", "submodule"):
            raise PathError("INVALID_SCHEMA", "provider supplied an unrecognized entry type")
        if name in tree or (path_key(name) in keys and keys[path_key(name)] != name):
            raise PathError("INVALID_PATH", "provider tree has ambiguous case/Unicode paths")
        tree[name] = entry
        keys[path_key(name)] = name

    for change in changes:
        action = change["action"]
        source = change.get("source")
        destination = change.get("destination") or change.get("path")
        for candidate in (source, destination):
            if candidate is None:
                continue
            if not covered(candidate, owned) or not covered(candidate, resource_scope):
                raise PathError("INVALID_SCOPE", "path outside assigned ownership")
            if covered(candidate, protected):
                raise PathError("PROTECTED_PATH", "path is protected")
            for i in range(1, len(candidate.split("/"))):
                ancestor = "/".join(candidate.split("/")[:i])
                existing = keys.get(path_key(ancestor))
                if existing is not None:
                    entry = tree[existing]
                    if existing != ancestor or getattr(entry, "kind") != "tree":
                        raise PathError("PROTECTED_PATH", "ambiguous, file or linked parent")
        if source is not None:
            observed = keys.get(path_key(source))
            if observed != source or getattr(tree[observed], "kind") not in ("blob",):
                raise PathError("INVALID_PATH", "source does not resolve to a normal file")
        if action == "modify":
            observed = keys.get(path_key(destination))
            if observed != destination or getattr(tree[observed], "kind") != "blob":
                raise PathError("INVALID_PATH", "modified path is not a normal existing file")
        if action in ("add", "rename", "copy"):
            if path_key(destination) in keys:
                raise PathError("INVALID_PATH", "destination exists or has a confusable twin")
