"""Turning a list of effects into something worth reading.

The layout owes an obvious debt to ``terraform plan``, for the same reason that
tool settled on it: a person scanning a change wants the symbol first, the thing
second, and the reason they should care last. Sorting is by severity rather than
by time, so the line that deletes a directory is never below the line that wrote
a log file.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Sequence

from consequence.effects import Effect, Severity, verb_of

_RESET = "\033[0m"
_STYLES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}

#: One character per severity, in the shape of a diff.
MARKS = {
    Severity.READ: ".",
    Severity.CREATE: "+",
    Severity.MODIFY: "~",
    Severity.DESTROY: "-",
    Severity.EXTERNAL: ">",
}

COLOURS = {
    Severity.READ: "dim",
    Severity.CREATE: "green",
    Severity.MODIFY: "cyan",
    Severity.DESTROY: "red",
    Severity.EXTERNAL: "yellow",
}


def colour_enabled(stream: object | None = None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream or sys.stdout, "isatty", lambda: False)())


def style(text: str, *names: str, enabled: bool | None = None) -> str:
    if enabled is None:
        enabled = colour_enabled()
    if not enabled or not names:
        return text
    return "".join(_STYLES.get(n, "") for n in names) + text + _RESET


def plain(text: str) -> str:
    out, escaping = [], False
    for char in text:
        if escaping:
            escaping = char != "m"
            continue
        if char == "\033":
            escaping = True
            continue
        out.append(char)
    return "".join(out)


def shorten_path(path: str) -> str:
    """Shorten a path against the working directory, without inventing `..` chains.

    Both sides are resolved before comparing, because on macOS the working
    directory reported by getcwd and the path recorded by an interceptor can be
    the same place spelled two ways.
    """
    from pathlib import Path as _Path

    try:
        here = str(_Path.cwd().resolve())
        full = str(_Path(path).resolve()) if _Path(path).is_absolute() else path
    except (OSError, RuntimeError):  # pragma: no cover
        return path
    if full.startswith(here + os.sep):
        return full[len(here) + 1 :]
    if not _Path(path).is_absolute():
        return path
    # normpath rather than resolve: it collapses the doubled separator a
    # sqlite:////path URL leaves behind, without turning /tmp into /private/tmp
    # and making every macOS path unrecognisable to the person reading it.
    tidy = os.path.normpath(path)
    # normpath keeps a leading "//", which POSIX reserves and nobody means. On
    # Windows the same shape is a UNC path, where the two separators name a host
    # and dropping one points the path somewhere else entirely.
    if os.sep == "/" and tidy.startswith("//"):
        return "/" + tidy.lstrip("/")
    return tidy


#: Where a target stops being readable and starts being a wall.
TARGET_WIDTH = 52


def _shorten(target: str) -> str:
    """Keep the end of a long path, since that is the part that identifies it."""
    if len(target) <= TARGET_WIDTH:
        return target
    return "..." + target[-(TARGET_WIDTH - 3) :]


def render(
    effects: Sequence[Effect],
    *,
    title: str = "",
    show_reads: bool = False,
    limit: int = 0,
    colour: bool | None = None,
) -> str:
    """The main view: one line per effect, worst first."""
    enabled = colour_enabled() if colour is None else colour
    chosen = [e for e in effects if show_reads or e.severity > Severity.READ]
    chosen.sort(key=lambda e: (-int(e.severity), e.at))
    hidden = 0
    if limit and len(chosen) > limit:
        hidden = len(chosen) - limit
        chosen = chosen[:limit]

    lines: list[str] = []
    if title:
        lines.append(style(title, "bold", enabled=enabled))
        lines.append("")
    if not chosen:
        lines.append(style("  no effects", "dim", enabled=enabled))
        return "\n".join(lines)

    # Truncated before the width is measured, not after: capping the width
    # alone leaves a long path or URL stretching its own line to whatever length
    # it happens to be, and the columns stop lining up.
    targets = [_shorten(shorten_path(e.target)) for e in chosen]
    width = max((len(t) for t in targets), default=0)
    for effect, target in zip(chosen, targets, strict=False):
        mark = MARKS.get(effect.severity, "?")
        colour_name = COLOURS.get(effect.severity, "")
        head = style(f"  {mark} {target:<{width}}", colour_name, enabled=enabled)
        detail = effect.detail or verb_of(effect.kind)
        where = (
            style(f"  {shorten_path(str(effect.origin))}", "dim", enabled=enabled)
            if effect.origin
            else ""
        )
        flag = ""
        if not effect.allowed:
            flag = "  " + style("DENIED", "red", "bold", enabled=enabled)
        elif effect.severity >= Severity.DESTROY:
            flag = "  " + style(effect.severity.label.upper(), colour_name, enabled=enabled)
        lines.append(f"{head}  {detail[:44]:<44}{where}{flag}")

    if hidden:
        lines.append(style(f"  ... and {hidden} more", "dim", enabled=enabled))
    return "\n".join(lines)


#: The same fact in two tenses, because "1 to destroy" and "1 destroyed" are
#: very different sentences to read after the fact.
_PAST = {
    "to create": "created",
    "to change": "changed",
    "to destroy": "destroyed",
    "external": "external",
}


def _tense(word: str, performed: bool) -> str:
    return _PAST[word] if performed else word


def summary(effects: Iterable[Effect], *, performed: bool, colour: bool | None = None) -> str:
    """The bottom line, in the tense that matches what actually happened."""
    enabled = colour_enabled() if colour is None else colour
    counts: dict[Severity, int] = {}
    denied = 0
    for effect in effects:
        if not effect.allowed:
            # Counted as a denial, not as something that happened. A refused
            # delete belongs in neither "to destroy" nor "destroyed".
            denied += 1
            continue
        counts[effect.severity] = counts.get(effect.severity, 0) + 1

    parts = []
    for severity, word in (
        (Severity.CREATE, "to create"),
        (Severity.MODIFY, "to change"),
        (Severity.DESTROY, "to destroy"),
        (Severity.EXTERNAL, "external"),
    ):
        count = counts.get(severity, 0)
        if count:
            parts.append(
                style(f"{count} {_tense(word, performed)}", COLOURS[severity], enabled=enabled)
            )

    headline = ", ".join(parts) if parts else "nothing"
    verb = "Performed" if performed else "Plan"
    lines = [f"{style(verb + ':', 'bold', enabled=enabled)} {headline}."]

    destructive = counts.get(Severity.DESTROY, 0)
    if destructive and not performed:
        lines.append(
            style(
                f"{destructive} destructive effect(s). Nothing was performed.",
                "red",
                enabled=enabled,
            )
        )
    if denied:
        lines.append(style(f"{denied} effect(s) denied by policy.", "red", "bold", enabled=enabled))
    return "\n".join(lines)


def table(headers: Sequence[str], rows: Iterable[Sequence[str]], *, aligns: str = "") -> str:
    body = [list(map(str, row)) for row in rows]
    if not body:
        return ""
    widths = [len(h) for h in headers]
    for row in body:
        for i, cell in enumerate(row[: len(widths)]):
            widths[i] = max(widths[i], len(plain(cell)))
    aligns = (aligns + "l" * len(headers))[: len(headers)]

    def line(cells: Sequence[str], header: bool = False) -> str:
        parts = []
        for cell, width, align in zip(cells, widths, aligns, strict=False):
            pad = width - len(plain(cell))
            parts.append(" " * pad + cell if align == "r" else cell + " " * pad)
        text = "  ".join(parts).rstrip()
        return style(text, "bold") if header else text

    return "\n".join(
        [line(headers, header=True), "  ".join("-" * w for w in widths), *(line(r) for r in body)]
    )
