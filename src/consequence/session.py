"""The active session: what mode we are in, what is allowed, and what happened.

One session is active at a time, per thread. Interceptors ask it what to do and
it answers with a decision and a record. Everything else in the package is either
producing effects for this thing or reading them back out of it.
"""

from __future__ import annotations

import contextvars
import os
import sys
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from consequence.effects import Effect, Frame, Severity, severity_of
from consequence.errors import Blocked, Denied
from consequence.overlay import Overlay
from consequence.policy import Policy, permissive


class Mode(str, Enum):
    PLAN = "plan"
    """Nothing reaches the outside world. Writes go to an overlay so the program
    still sees its own changes."""

    GUARD = "guard"
    """Effects happen, but only the ones policy allows. The rest raise."""

    AUDIT = "audit"
    """Everything happens and everything is written down."""


class Network(str, Enum):
    BLOCK = "block"
    """Plan mode refuses to reach the network, because it cannot invent a reply."""

    ALLOW = "allow"
    """Let real network calls through during a plan. Reads are often harmless and
    a program that cannot fetch anything cannot get far enough to be planned."""


_active: contextvars.ContextVar[Session | None] = contextvars.ContextVar(
    "consequence_session", default=None
)


def active() -> Session | None:
    return _active.get()


@dataclass
class Session:
    """One run under observation."""

    mode: Mode = Mode.AUDIT
    policy: Policy = field(default_factory=permissive)
    network: Network = Network.BLOCK
    effects: list[Effect] = field(default_factory=list)
    overlay: Overlay = field(default_factory=Overlay)
    #: Paths belonging to consequence itself and the interpreter, which are noise.
    quiet: bool = True
    _token: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- lifecycle ------------------------------------------------------------

    def __enter__(self) -> Session:
        from consequence import intercept

        self._token = _active.set(self)
        intercept.install()
        return self

    def __exit__(self, *exc: object) -> None:
        from consequence import intercept

        intercept.uninstall()
        if self._token is not None:
            _active.reset(self._token)
            self._token = None

    # --- the question every interceptor asks ----------------------------------

    def check(
        self,
        kind: str,
        target: str,
        detail: str = "",
        *,
        severity: Severity | None = None,
        **extra: Any,
    ) -> Effect:
        """Record an effect and decide what happens to it.

        Always returns; never silently swallows. The caller inspects
        ``effect.allowed`` and ``effect.performed`` to know what to do next, and
        raises through :meth:`enforce` when it wants the program stopped.
        """
        frames, internal = _frames(quiet=self.quiet)
        effect = Effect(
            kind=kind,
            target=target,
            detail=detail,
            # `is None`, not truthiness: Severity.READ is 0, so `severity or
            # severity_of(kind)` throws away an explicit READ and records the
            # per-kind default instead. Every caller that overrides today
            # happens to pass a severity matching its kind's default, so this
            # was invisible; the next one would not have been.
            severity=severity_of(kind) if severity is None else severity,
            frames=frames,
            internal=internal,
            extra=dict(extra),
        )
        decision = self.policy.decide(effect)
        effect.allowed = decision.allowed
        effect.reason = decision.reason
        effect.performed = decision.allowed and self.mode is not Mode.PLAN
        with self._lock:
            self.effects.append(effect)
        return effect

    def enforce(self, effect: Effect) -> None:
        """Stop the program if policy said no. Plan mode never gets this far."""
        if not effect.allowed:
            raise Denied(effect)

    def block(self, effect: Effect, hint: str = "") -> None:
        raise Blocked(effect, hint)

    @property
    def planning(self) -> bool:
        return self.mode is Mode.PLAN

    # --- reading the result ---------------------------------------------------

    def where(
        self,
        kind: str | Sequence[str] | None = None,
        *,
        min_severity: Severity | None = None,
        denied: bool | None = None,
    ) -> list[Effect]:
        kinds = {kind} if isinstance(kind, str) else set(kind or ())
        return [
            e
            for e in self.effects
            if (not kinds or e.kind in kinds)
            and (min_severity is None or e.severity >= min_severity)
            and (denied is None or e.allowed is not denied)
        ]

    @property
    def destructive(self) -> list[Effect]:
        return [e for e in self.effects if e.destructive]

    @property
    def denied(self) -> list[Effect]:
        return [e for e in self.effects if not e.allowed]

    def counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for effect in self.effects:
            totals[effect.kind] = totals.get(effect.kind, 0) + 1
        return dict(sorted(totals.items()))

    def to_json(self) -> list[dict[str, Any]]:
        return [e.to_json() for e in self.effects]

    def report(self, *, title: str = "", show_reads: bool = False, limit: int = 0) -> str:
        """The whole run as the CLI would print it, for putting somewhere else."""
        from consequence.report import render, summary

        body = render(self.effects, title=title, show_reads=show_reads, limit=limit)
        return f"{body}\n\n{summary(self.effects, performed=self.mode is not Mode.PLAN)}"

    def print(self, *, title: str = "", show_reads: bool = False, limit: int = 0) -> None:
        """Print the report. The last line of most scripts that use this package."""
        print(self.report(title=title, show_reads=show_reads, limit=limit))


# --- call sites ---------------------------------------------------------------

#: Directories whose frames are never interesting: this package, the standard
#: library, and site-packages. What a person wants to see is their own line.
_NOISE = (
    str(Path(__file__).resolve().parent),
    str(Path(os.__file__).parent),
)


def _frames(quiet: bool = True, limit: int = 12) -> tuple[list[Frame], bool]:
    """The call stack trimmed to code the caller wrote, and whether any was left.

    An empty result is a fact worth carrying rather than a problem to paper over.
    It means nothing in the stack belonged to the caller: the effect came from
    the import system, or from a framework doing its own housekeeping around
    them. The frames are handed back anyway, because an effect nobody can locate
    is useless, but the second half of the return value says not to blame the
    caller for it.
    """
    found: list[Frame] = []
    frame: Any = sys._getframe(2)
    while frame is not None and len(found) < limit:
        filename = frame.f_code.co_filename
        if not (quiet and _is_noise(filename)):
            found.append(Frame(file=filename, line=frame.f_lineno, function=frame.f_code.co_name))
        frame = frame.f_back
    if found:
        return found, False

    frame = sys._getframe(2)
    if frame is not None:
        found.append(
            Frame(
                file=frame.f_code.co_filename,
                line=frame.f_lineno,
                function=frame.f_code.co_name,
            )
        )
    return found, True


def _is_noise(filename: str) -> bool:
    if not filename or filename.startswith("<"):
        return True
    if "site-packages" in filename or "dist-packages" in filename:
        return True
    return any(filename.startswith(root) for root in _NOISE)


def iter_effects(session: Session) -> Iterator[Effect]:
    yield from session.effects
