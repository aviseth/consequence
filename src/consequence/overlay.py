"""A copy-on-write view of the filesystem, so plan mode can be honest.

The naive way to build a dry run is to swallow every write. That produces a plan
which is wrong the moment the program reads back something it just wrote, which
real programs do constantly: write a config, read it, branch on it. The plan then
follows a path the real run never would, and quietly describes a different
program.

So writes in plan mode land here instead of on disk, and reads consult this first
and fall through to the real filesystem when it has nothing to say. The program
sees the world it created. The disk does not change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _key(path: str | os.PathLike[str]) -> str:
    """One spelling per file, so /tmp/a and /private/tmp/./a are the same entry."""
    text = os.fspath(path)
    try:
        return str(Path(text).expanduser().resolve())
    except (OSError, RuntimeError):  # pragma: no cover
        return os.path.abspath(text)


@dataclass
class Overlay:
    """Writes and deletions that have not happened."""

    files: dict[str, bytes] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    directories: set[str] = field(default_factory=set)

    # --- what the program did -------------------------------------------------

    def write(self, path: str | os.PathLike[str], data: bytes) -> None:
        key = _key(path)
        self.files[key] = data
        self.deleted.discard(key)
        # Only a parent that is not already there counts as a directory this
        # run would create. Recording every parent made a plan that writes one
        # file into an existing directory claim it would create that directory.
        parent = str(Path(key).parent)
        if parent and parent != key and not os.path.isdir(parent):
            self.directories.add(parent)

    def append(self, path: str | os.PathLike[str], data: bytes) -> None:
        key = _key(path)
        self.files[key] = self.read(key) or b"" if key not in self.deleted else b""
        self.files[key] += data
        self.deleted.discard(key)

    def delete(self, path: str | os.PathLike[str]) -> None:
        key = _key(path)
        self.files.pop(key, None)
        self.directories.discard(key)
        self.deleted.add(key)

    def delete_tree(self, path: str | os.PathLike[str]) -> None:
        root = _key(path) + os.sep
        for known in [k for k in self.files if k.startswith(root)]:
            self.files.pop(known, None)
        self.directories = {d for d in self.directories if not d.startswith(root)}
        self.deleted.add(_key(path))

    def mkdir(self, path: str | os.PathLike[str]) -> None:
        key = _key(path)
        self.directories.add(key)
        self.deleted.discard(key)

    def move(self, src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        data = self.read(src)
        if data is not None:
            self.write(dst, data)
        self.delete(src)

    # --- what the program sees ------------------------------------------------

    def read(self, path: str | os.PathLike[str]) -> bytes | None:
        """Contents according to the overlay, or None to fall through to disk."""
        key = _key(path)
        if key in self.files:
            return self.files[key]
        if key in self.deleted:
            return None
        try:
            return Path(key).read_bytes()
        except OSError:
            return None

    def exists(self, path: str | os.PathLike[str]) -> bool:
        key = _key(path)
        if key in self.files or key in self.directories:
            return True
        if key in self.deleted or self._under_deleted(key):
            return False
        return os.path.exists(key)

    def _under_deleted(self, key: str) -> bool:
        return any(key.startswith(root + os.sep) for root in self.deleted)

    # --- what changed ---------------------------------------------------------

    @property
    def touched(self) -> set[str]:
        return set(self.files) | self.deleted | self.directories

    def summary(self) -> str:
        parts = []
        if self.files:
            parts.append(f"{len(self.files)} file(s) written")
        if self.deleted:
            parts.append(f"{len(self.deleted)} deleted")
        if self.directories:
            parts.append(f"{len(self.directories)} directory/directories created")
        return ", ".join(parts) or "nothing"
