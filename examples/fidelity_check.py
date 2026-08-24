"""Check a plan against reality, on your own program.

    uv run python examples/fidelity_check.py --seed ./project -- -m mypkg --flag

Runs the program twice from identical copies of ``--seed``: once under plan
mode, once for real. Then it asks two different questions.

  safety      did the plan change anything on disk?
  fidelity    did the plan predict every effect the real run had?

The demo in this directory shows that the tool runs. This shows whether it is
telling you the truth about *your* program, which is the only question that
matters before you rely on it. Point it at a copy, never at anything you care
about: the second run is real.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def manifest(root: Path) -> dict[str, str]:
    """Every file under root, by content. The part a run cannot fake."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def consequence_argv(mode: str, target: list[str]) -> list[str]:
    """Put the program's own arguments behind a "--" so consequence leaves them alone."""
    head, rest = target[:1], target[1:]
    if head and head[0] in ("-m", "--module"):
        head, rest = target[:2], target[2:]
    return [sys.executable, "-m", "consequence", mode, "--json", *head, "--", *rest]


def effects(mode: str, target: list[str], cwd: Path) -> list[dict]:
    result = subprocess.run(
        consequence_argv(mode, target),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        sys.stderr.write(result.stdout[-2000:] + result.stderr[-2000:])
        raise SystemExit(f"{mode} failed with {result.returncode}")
    # The program prints to stdout too, so find the report rather than assuming
    # consequence had the stream to itself.
    start = result.stdout.rindex("\n{\n") + 1 if "\n{\n" in result.stdout else 0
    report = json.loads(result.stdout[start:])
    if report["failed"]:
        # Without this the checker is happy to compare nothing against nothing
        # and call it a perfect plan. A target that could not even be imported
        # is the most likely way to get a green result you have not earned.
        sys.stderr.write(result.stderr[-2000:])
        raise SystemExit(f"the program failed under {mode}: {report['failed']}")
    return list(report["effects"])


def identity(effect: dict) -> tuple[str, str, str]:
    """Two effects are the same effect if these three things match."""
    return (effect["kind"], Path(effect["target"]).name, effect["detail"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", type=Path, required=True, help="a directory to copy from")
    parser.add_argument("--work", type=Path, default=Path("./fidelity-work"))
    # Split on the first "--" by hand. argparse will not hold a REMAINDER that
    # starts with -m, and the same trap once cost consequence's own CLI the
    # first flag of every program it ran with -m.
    words = list(sys.argv[1:] if argv is None else argv)
    target = words[words.index("--") + 1 :] if "--" in words else []
    args = parser.parse_args(words[: words.index("--")] if "--" in words else words)
    if not target:
        parser.error("put the program after --, e.g. --seed ./project -- -m mypkg up")

    def reset() -> None:
        if args.work.exists():
            shutil.rmtree(args.work)
        shutil.copytree(args.seed, args.work)

    reset()
    before = manifest(args.work)
    planned = effects("plan", target, args.work)
    after_plan = manifest(args.work)

    reset()
    real = effects("audit", target, args.work)

    print(f"planned {len(planned)} effect(s); the real run had {len(real)}\n")

    ok = True
    if before == after_plan:
        print("safety    the plan changed nothing on disk")
    else:
        ok = False
        touched = sorted({name for name, _ in set(before.items()) ^ set(after_plan.items())})
        print(f"safety    FAILED, the plan touched {len(touched)} file(s):")
        for name in touched[:10]:
            print(f"            {name}")

    predicted = {identity(e) for e in planned}
    happened = {identity(e) for e in real}
    missed = sorted(happened - predicted)
    if not missed:
        print(f"fidelity  every one of the {len(happened)} real effect(s) was predicted")
    else:
        ok = False
        print(f"fidelity  FAILED, {len(missed)} real effect(s) the plan did not predict:")
        for kind, name, detail in missed[:15]:
            print(f"            {kind:<14} {name}  {detail}")

    surplus = sorted(predicted - happened)
    if surplus:
        # Not a failure. A plan is allowed to be cautious, and a program that
        # branches on the clock or on a fresh directory will differ between runs.
        print(f"          ({len(surplus)} planned effect(s) the real run did not have)")
        for kind, name, detail in surplus[:10]:
            print(f"            {kind:<14} {name}  {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
