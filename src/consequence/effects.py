"""What an effect is, and how much damage it could do.

An effect is anything a program does that the outside world notices: a file
written, a process spawned, a socket opened, a table dropped. Everything else is
just arithmetic, and arithmetic cannot page anybody at three in the morning.

Each one carries enough to answer three questions without re-running anything:
what would happen, where in the code it came from, and how bad it would be if it
were wrong.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

# --- kinds -------------------------------------------------------------------

FILE_READ = "file.read"
FILE_WRITE = "file.write"
FILE_APPEND = "file.append"
FILE_DELETE = "file.delete"
FILE_MOVE = "file.move"
FILE_COPY = "file.copy"
DIR_CREATE = "dir.create"
DIR_DELETE = "dir.delete"
PERM_CHANGE = "perm.change"
PROCESS_SPAWN = "process.spawn"
PROCESS_SIGNAL = "process.signal"
NET_CONNECT = "net.connect"
NET_REQUEST = "net.request"
DB_QUERY = "db.query"
DB_MUTATE = "db.mutate"
DB_SCHEMA = "db.schema"
ENV_CHANGE = "env.change"


class Severity(IntEnum):
    """How much it would matter if this were a mistake.

    The ordering is the point: a plan sorted by severity puts the thing that
    deletes your home directory above the thing that writes a log line, which is
    the order a person reads in.
    """

    READ = 0
    """Observes the world without changing it."""
    CREATE = 1
    """Adds something that was not there. Undone by deleting it."""
    MODIFY = 2
    """Changes something that existed. The previous contents are at risk."""
    DESTROY = 3
    """Removes something. Not reversible without a copy taken beforehand."""
    EXTERNAL = 4
    """Reaches a system this process does not own, where undo is not ours to do."""

    @property
    def label(self) -> str:
        return self.name.lower()


#: Default severity per kind. A policy can raise one but never lower it: the
#: point of a floor is that nobody can quietly opt out of it.
SEVERITY: dict[str, Severity] = {
    FILE_READ: Severity.READ,
    FILE_WRITE: Severity.MODIFY,
    FILE_APPEND: Severity.MODIFY,
    FILE_DELETE: Severity.DESTROY,
    FILE_MOVE: Severity.MODIFY,
    FILE_COPY: Severity.CREATE,
    DIR_CREATE: Severity.CREATE,
    DIR_DELETE: Severity.DESTROY,
    PERM_CHANGE: Severity.MODIFY,
    PROCESS_SPAWN: Severity.EXTERNAL,
    PROCESS_SIGNAL: Severity.EXTERNAL,
    NET_CONNECT: Severity.EXTERNAL,
    NET_REQUEST: Severity.EXTERNAL,
    DB_QUERY: Severity.READ,
    DB_MUTATE: Severity.MODIFY,
    DB_SCHEMA: Severity.DESTROY,
    ENV_CHANGE: Severity.MODIFY,
}


@dataclass(frozen=True)
class Frame:
    """One line of the call site, filtered down to code somebody wrote."""

    file: str
    line: int
    function: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line} in {self.function}"


@dataclass
class Effect:
    """One thing the program did, or would have done."""

    kind: str
    target: str
    detail: str = ""
    severity: Severity = Severity.MODIFY
    frames: list[Frame] = field(default_factory=list)
    at: float = field(default_factory=time.time)
    allowed: bool = True
    reason: str = ""
    performed: bool = False
    internal: bool = False
    """No frame in this effect's stack belongs to code the caller wrote.

    It came from the interpreter, or from a framework running around them:
    pytest creating a tmp_path, the import system writing a __pycache__. Still
    recorded, because hiding an effect entirely is how a tool loses trust, but
    it is not something the caller did and should not be reported as such.
    """
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def origin(self) -> Frame | None:
        """The nearest frame in the caller's own code."""
        return self.frames[0] if self.frames else None

    @property
    def destructive(self) -> bool:
        return self.severity >= Severity.DESTROY

    def describe(self) -> str:
        """One line, in the tense that matches whether it happened."""
        verb = _VERBS.get(self.kind, self.kind)
        detail = f" {self.detail}" if self.detail else ""
        return f"{verb} {self.target}{detail}"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "detail": self.detail,
            "severity": self.severity.label,
            "allowed": self.allowed,
            "reason": self.reason,
            "performed": self.performed,
            "internal": self.internal,
            "at": self.at,
            "where": str(self.origin) if self.origin else "",
            "stack": [str(f) for f in self.frames],
            **({"extra": self.extra} if self.extra else {}),
        }


_VERBS = {
    FILE_READ: "read",
    FILE_WRITE: "write",
    FILE_APPEND: "append to",
    FILE_DELETE: "delete",
    FILE_MOVE: "move",
    FILE_COPY: "copy",
    DIR_CREATE: "create directory",
    DIR_DELETE: "delete directory",
    PERM_CHANGE: "change permissions on",
    PROCESS_SPAWN: "run",
    PROCESS_SIGNAL: "signal",
    NET_CONNECT: "connect to",
    NET_REQUEST: "request",
    DB_QUERY: "query",
    DB_MUTATE: "modify rows in",
    DB_SCHEMA: "alter schema of",
    ENV_CHANGE: "set environment",
}


def verb_of(kind: str) -> str:
    """The human word for a kind, for when an effect carries no extra detail."""
    return _VERBS.get(kind, kind)


def severity_of(kind: str) -> Severity:
    return SEVERITY.get(kind, Severity.MODIFY)
