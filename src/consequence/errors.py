"""Exceptions, and one of them is the whole point of the library."""

from __future__ import annotations

from consequence.effects import Effect


class ConsequenceError(Exception):
    """Base class for everything raised here."""


def _where(effect: Effect) -> str:
    """The call site, with the file shortened against the working directory."""
    from consequence.report import shorten_path

    frame = effect.origin
    if frame is None:  # pragma: no cover - callers check first
        return ""
    return f"{shorten_path(frame.file)}:{frame.line} in {frame.function}"


class Denied(ConsequenceError):
    """Policy refused an effect.

    Raised at the call site, so the traceback points at the line that tried, not
    at the policy. Somebody reading the failure should not have to work out which
    of forty file writes was the one that got stopped.
    """

    def __init__(self, effect: Effect) -> None:
        where = f" at {_where(effect)}" if effect.origin else ""
        super().__init__(f"refused to {effect.describe(short=True)}{where}: {effect.reason}")
        self.effect = effect


class Blocked(ConsequenceError):
    """Plan mode could not simulate something, so it stopped rather than guess.

    Network is the case that matters. A write can be held in an overlay and a
    subprocess can be reported as having succeeded, but there is no honest way to
    invent the body of an HTTP response. Making one up would produce a plan for a
    program that does not exist.
    """

    def __init__(self, effect: Effect, hint: str = "") -> None:
        where = f" at {_where(effect)}" if effect.origin else ""
        super().__init__(
            f"plan mode cannot simulate: {effect.describe(short=True)}{where}."
            + (f" {hint}" if hint else "")
        )
        self.effect = effect
