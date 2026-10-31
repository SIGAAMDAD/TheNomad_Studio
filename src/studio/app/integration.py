"""One-tree, local byte-exact integration with atomic revision compare-and-swap.

This adapter performs local file integration only. It does not assert GitHub
publication or QA approval. In production, WG-G must attest the remote source
bytes and WG-E/L must attest independent QA before promote() is called.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

from .errors import StudioError
from .intake import Handoff, digest, normalized


@dataclass(frozen=True)
class Candidate:
    intent_id: str
    project_id: str
    expected_revision_id: str
    revision_id: str
    submission_ids: tuple[str, ...]
    tree_digest: str
    tree: dict[str, bytes]
    author_ids: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedQA:
    project_id: str
    candidate_revision_id: str
    submission_id: str
    reviewer_principal_id: str
    author_shift_id: str
    review_id: str
    verdict: str
    evidence_digest: str


class IndependentQAVerifier(Protocol):
    def verify(self, *, project_id: str, revision_id: str,
               submission_ids: tuple[str, ...],
               authors: tuple[str, ...], review_ids: tuple[str, ...]) -> bool:
        """Confirm persisted independent WG-E/L QA on exact bytes, not claims."""
        ...


class SourceReadback(Protocol):
    def verify_exact(self, *, project_id: str, revision_id: str,
                     tree_digest: str, tree: dict[str, bytes]) -> bool:
        """WG-G authenticated source GitHub SHA/remote bytes verification."""
        ...


def tree_digest(tree: dict[str, bytes]) -> str:
    entries = sorted((normalized(path), digest(data)) for path, data in tree.items())
    return digest(json.dumps(entries, separators=(",", ":")).encode("utf-8"))


def _write_tree(path: Path, tree: dict[str, bytes]) -> None:
    path.mkdir(parents=True, exist_ok=False)
    for name, data in sorted(tree.items()):
        target = path / normalized(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _read_tree(path: Path) -> dict[str, bytes]:
    if not path.is_dir():
        raise StudioError("missing", "candidate tree is absent")
    result: dict[str, bytes] = {}
    for file in path.rglob("*"):
        if file.is_symlink():
            raise StudioError("invalid", "symlink in source snapshot")
        if file.is_file():
            rel = file.relative_to(path).as_posix()
            result[normalized(rel)] = file.read_bytes()
    return result


@contextmanager
def _exclusive_lock(file: Path):
    """Portable advisory lock for a single canonical tree's pointer."""
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\x00")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class CanonicalTree:
    """Filesystem-backed local tree CAS, one per *project*.

    Persisted M integration state; C still owns authoritative SQL tables. A
    production coordinator must commit source_revisions using C's named domain
    transaction and verify WG-G's remote publication separately.
    """

    def __init__(self, root: Path, project_id: str):
        if not project_id or "/" in project_id or "\\" in project_id or project_id in (".", ".."):
            raise StudioError("invalid", "invalid project scope")
        self.root = root.resolve() / project_id
        self.revisions = self.root / "revisions"
        self.stages = self.root / "stages"
        self.pointer = self.root / "canonical.json"
        self._lock = self.root / ".canonical.lock"
        self._in_process = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.revisions.mkdir(exist_ok=True)
        self.stages.mkdir(exist_ok=True)
        with self._in_process, _exclusive_lock(self._lock):
            if not self.pointer.exists():
                empty_id = tree_digest({})
                _write_tree(self.revisions / empty_id, {})
                self._replace_pointer(empty_id)

    def _replace_pointer(self, revision_id: str) -> None:
        with tempfile.NamedTemporaryFile(dir=self.root, prefix="pointer-", delete=False) as f:
            path = Path(f.name)
            f.write(json.dumps({"revision_id": revision_id}).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(path, self.pointer)

    def current_revision(self) -> str:
        return json.loads(self.pointer.read_text(encoding="utf-8"))["revision_id"]

    def read_revision(self, revision_id: str) -> dict[str, bytes]:
        if not isinstance(revision_id, str) or len(revision_id) != 64 or any(c not in "0123456789abcdef" for c in revision_id):
            raise StudioError("invalid", "invalid local source revision identifier")
        tree = _read_tree(self.revisions / revision_id)
        if tree_digest(tree) != revision_id:
            raise StudioError("invalid", "revision tree bytes do not match identifier")
        return tree

    def stage(self, *, intent_id: str, project_id: str,
              submissions: Iterable[tuple[str, str, Handoff]]) -> Candidate:
        if project_id != self.root.name:
            raise StudioError("forbidden", "cross-project integration")
        if not intent_id or not intent_id.isascii() or not all(c.isalnum() or c in "-_" for c in intent_id):
            raise StudioError("invalid", "invalid integration intent")
        submissions = tuple(submissions)
        if not submissions:
            raise StudioError("invalid", "no verified handoffs")
        with self._in_process, _exclusive_lock(self._lock):
            expected = self.current_revision()
            base = self.read_revision(expected)
            merged = base.copy()
            changed: set[str] = set()
            ids: list[str] = []
            authors: list[str] = []
            for submission_id, author_id, handoff in submissions:
                if not submission_id or submission_id in ids or not author_id:
                    raise StudioError("invalid", "duplicate submission or missing author")
                initial_symbol = (handoff.base_revision_id == "EMPTY_NEW_SOURCE_TREE"
                                  and expected == tree_digest({}))
                if handoff.base_revision_id != expected and not initial_symbol:
                    raise StudioError("stale_revision", "handoff baseline differs from canonical revision")
                ids.append(submission_id)
                authors.append(author_id)
                for change in handoff.changes:
                    if change.path in changed:
                        raise StudioError("conflict", f"overlapping worker paths: {change.path}")
                    changed.add(change.path)
                    old = base.get(change.path)
                    if (digest(old) if old is not None else None) != change.prior_sha256:
                        raise StudioError("conflict", f"three-way path changed since baseline: {change.path}")
                    if change.new_sha256 is None:
                        merged.pop(change.path, None)
                    else:
                        if change.content is None or digest(change.content) != change.new_sha256:
                            raise StudioError("invalid", "manifest bytes changed since intake")
                        merged[change.path] = change.content
            revision_id = tree_digest(merged)
            stage_dir = self.stages / intent_id
            if stage_dir.exists():
                existing = self.inspect(intent_id)
                if (existing.project_id == project_id and
                        existing.expected_revision_id == expected and
                        existing.revision_id == revision_id and
                        existing.submission_ids == tuple(ids) and
                        existing.author_ids == tuple(authors) and
                        existing.tree == merged):
                    return existing
                raise StudioError("conflict", "integration request replay changed content")
            tmp = Path(tempfile.mkdtemp(prefix="stage-", dir=self.root))
            try:
                # Store source in a separate directory, and a manifest to replay
                # the staged candidate without trusting mutable Python state.
                _write_tree(tmp / "tree", merged)
                (tmp / "intent.json").write_text(json.dumps({
                    "project_id": project_id,
                    "expected_revision_id": expected,
                    "revision_id": revision_id,
                    "tree_digest": revision_id,
                    "submission_ids": ids,
                    "authors": authors,
                }, sort_keys=True), encoding="utf-8")
                os.replace(tmp, stage_dir)
            finally:
                if tmp.exists():
                    shutil.rmtree(tmp)
            return Candidate(intent_id, project_id, expected, revision_id,
                             tuple(ids), revision_id, merged, tuple(authors))

    def inspect(self, intent_id: str) -> Candidate:
        stage_dir = self.stages / intent_id
        if not stage_dir.is_dir():
            raise StudioError("missing", "integration intent not staged")
        meta = json.loads((stage_dir / "intent.json").read_text(encoding="utf-8"))
        tree = _read_tree(stage_dir / "tree")
        if tree_digest(tree) != meta["revision_id"] or meta["project_id"] != self.root.name:
            raise StudioError("invalid", "staged bytes/metadata changed")
        return Candidate(intent_id, meta["project_id"], meta["expected_revision_id"],
                         meta["revision_id"], tuple(meta["submission_ids"]),
                         meta["tree_digest"], tree, tuple(meta["authors"]))

    def recover_local_pointer(self, authoritative_revision: str, verified_tree: dict[str, bytes]) -> None:
        """Repair local mirror only with bytes verified against authoritative revision.

        The caller must have independently checked the C canonical revision
        and authenticated WG-G remote source readback before invoking this.
        """
        if tree_digest(verified_tree) != authoritative_revision:
            raise StudioError("invalid", "recovery bytes do not match authoritative digest")
        with self._in_process, _exclusive_lock(self._lock):
            destination = self.revisions / authoritative_revision
            if destination.exists():
                if _read_tree(destination) != verified_tree:
                    raise StudioError("invalid", "existing revision bytes corrupted")
            else:
                tmp = Path(tempfile.mkdtemp(prefix="recovery-", dir=self.root))
                try:
                    _write_tree(tmp / "tree", verified_tree)
                    os.replace(tmp / "tree", destination)
                finally:
                    if tmp.exists():
                        shutil.rmtree(tmp)
            self._replace_pointer(authoritative_revision)

    def promote(self, *, intent_id: str, review_ids: tuple[str, ...],
                qa: IndependentQAVerifier, readback: SourceReadback,
                expected_revision_id: str) -> str:
        """Promote exact verified candidate; reject stale pointer and unverified QA.

        Caller must persist matching C domain intent/source_revision CAS in the
        authoritative transaction; filesystem CAS here only coordinates local
        bytes and never claims a remote source push.
        """
        with self._in_process, _exclusive_lock(self._lock):
            candidate = self.inspect(intent_id)
            current = self.current_revision()
            if candidate.expected_revision_id != current or expected_revision_id != current:
                raise StudioError("stale_revision", "canonical pointer advanced")
            if not review_ids:
                raise StudioError("qa_failed", "no independent QA reviews")
            if not qa.verify(project_id=candidate.project_id,
                             revision_id=candidate.revision_id,
                             submission_ids=candidate.submission_ids,
                             authors=candidate.author_ids, review_ids=review_ids):
                raise StudioError("qa_failed", "independent exact-byte QA not verified")
            if not readback.verify_exact(project_id=candidate.project_id,
                                         revision_id=candidate.revision_id,
                                         tree_digest=candidate.tree_digest, tree=candidate.tree):
                raise StudioError("unknown_effect", "remote source publication/readback unverified")
            destination = self.revisions / candidate.revision_id
            if destination.exists():
                if _read_tree(destination) != candidate.tree:
                    raise StudioError("invalid", "content-addressed revision collision")
            else:
                tmp = Path(tempfile.mkdtemp(prefix="revision-", dir=self.root))
                try:
                    _write_tree(tmp / "tree", candidate.tree)
                    os.replace(tmp / "tree", destination)
                finally:
                    if tmp.exists():
                        shutil.rmtree(tmp)
            # Single locked compare-and-swap of exact canonical pointer bytes.
            if self.current_revision() != expected_revision_id:
                raise StudioError("stale_revision", "canonical pointer changed")
            self._replace_pointer(candidate.revision_id)
            return candidate.revision_id
