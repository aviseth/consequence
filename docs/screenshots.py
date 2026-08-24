"""Regenerate the terminal screenshots in the README.

    uv run python docs/screenshots.py

Every image is produced by running the real command against the demo project in
``examples/`` and converting the ANSI output to SVG. Nothing here is hand-drawn,
so a screenshot that stops matching the tool is one that stops being generated.
SVG rather than PNG because it stays sharp at any size, diffs as text, and costs
a few kilobytes.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent

#: Terminal palette, picked so red and yellow stay legible on dark grey.
COLOURS = {
    "31": "#ff7b72",
    "32": "#7ee787",
    "33": "#e3b341",
    "34": "#79c0ff",
    "35": "#d2a8ff",
    "36": "#56d4dd",
}
FOREGROUND = "#d8dee9"
BACKGROUND = "#1c2128"
CHROME = "#22272e"

#: Generous on purpose. The advance depends on which monospace font the viewer
#: resolves, and a box sized for a narrow one clips the right-hand column on
#: anything wider. Overshooting costs a strip of matching background; guessing
#: low costs the reader the part of the line that says where the effect came
#: from. textLength would pin it exactly, and is ignored by enough renderers
#: not to be worth relying on.
CHAR_WIDTH = 9.0
LINE_HEIGHT = 20
PADDING = 18
TITLE_BAR = 34

ANSI = re.compile(r"\033\[([0-9;]*)m")


class Style:
    """The SGR state, which is a couple of booleans and one colour."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.bold = False
        self.dim = False
        self.colour: str | None = None

    def apply(self, codes: str) -> None:
        for code in (codes or "0").split(";"):
            if code in ("", "0"):
                self.reset()
            elif code == "1":
                self.bold = True
            elif code == "2":
                self.dim = True
            elif code in COLOURS:
                self.colour = COLOURS[code]

    def attributes(self) -> str:
        parts = []
        if self.colour:
            parts.append(f'fill="{self.colour}"')
        if self.bold:
            parts.append('font-weight="bold"')
        if self.dim:
            parts.append('opacity="0.55"')
        return " ".join(parts)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _spans(line: str) -> tuple[str, int]:
    """One line of ANSI text as SVG tspans, and how many columns it occupies."""
    style = Style()
    out: list[str] = []
    columns = 0
    position = 0
    for match in ANSI.finditer(line):
        chunk = line[position : match.start()]
        if chunk:
            out.append(f'<tspan {style.attributes()} xml:space="preserve">{_escape(chunk)}</tspan>')
            columns += len(chunk)
        style.apply(match.group(1))
        position = match.end()
    tail = line[position:]
    if tail:
        out.append(f'<tspan {style.attributes()} xml:space="preserve">{_escape(tail)}</tspan>')
        columns += len(tail)
    return "".join(out), columns


def render(command: str, output: str) -> str:
    """A terminal window holding ``output``, with ``command`` on the prompt line."""
    lines = [f"\033[32m$\033[0m \033[1m{command}\033[0m", "", *output.rstrip("\n").split("\n")]
    body = []
    widest = 0
    for index, line in enumerate(lines):
        spans, columns = _spans(line)
        widest = max(widest, columns)
        y = TITLE_BAR + PADDING + (index + 1) * LINE_HEIGHT - 6
        body.append(f'<text x="{PADDING}" y="{y}">{spans}</text>')

    width = int(widest * CHAR_WIDTH) + PADDING * 2
    height = TITLE_BAR + PADDING * 2 + len(lines) * LINE_HEIGHT
    dots = "".join(
        f'<circle cx="{x}" cy="17" r="6" fill="{colour}"/>'
        for x, colour in ((20, "#ff5f57"), (40, "#febc2e"), (60, "#28c840"))
    )
    text = "\n".join("    " + line for line in body)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" \
viewBox="0 0 {width} {height}" font-family="SFMono-Regular,Menlo,Consolas,monospace" \
font-size="13.5">
  <rect width="{width}" height="{height}" rx="10" fill="{BACKGROUND}"/>
  <rect width="{width}" height="{TITLE_BAR}" rx="10" fill="{CHROME}"/>
  <rect y="{TITLE_BAR - 10}" width="{width}" height="10" fill="{CHROME}"/>
  {dots}
  <g fill="{FOREGROUND}">
{text}
  </g>
</svg>
"""


def capture(argv: list[str], cwd: Path) -> str:
    environment = dict(os.environ, FORCE_COLOR="1")
    environment.pop("NO_COLOR", None)
    result = subprocess.run(
        argv, check=False, cwd=cwd, capture_output=True, text=True, env=environment
    )
    return result.stdout + result.stderr


SHOTS = [
    ("plan", ["consequence", "plan", "cleanup_agent.py"]),
    ("guard", ["consequence", "run", "--policy", "safe.toml", "cleanup_agent.py"]),
    ("audit", ["consequence", "audit", "--log", "run.jsonl", "cleanup_agent.py"]),
]


def main() -> int:
    sys.path.insert(0, str(ROOT / "examples"))
    from setup_demo import build

    for name, argv in SHOTS:
        build()
        text = capture(argv, ROOT / "examples" / "demo")
        target = HERE / f"{name}.svg"
        target.write_text(render(" ".join(argv), text), encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)}")
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
