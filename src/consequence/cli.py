"""Command line interface.

Everything runs in-process. This is not a sandbox that spawns your program
somewhere safe; it loads it into this interpreter with the interceptors already
installed. That is what lets plan mode hand the program its own writes back, and
it is also the honest limit of the approach, which the README states plainly.
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path
from typing import Any

from consequence import __version__

EPILOG = """\
examples:
  consequence plan deploy.py
  consequence plan -m mypkg.cli -- --force
  consequence run --policy safe.toml agent_script.py
  consequence check --policy safe.toml deploy.py
  consequence audit --log run.jsonl deploy.py
  consequence log run.jsonl --destructive
"""


def _rows(text: str) -> int:
    """A row count, because a negative one silently drops rows off the report."""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"--limit cannot be negative, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="consequence",
        description="See what your code would do before it does it.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"consequence {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def target(p: argparse.ArgumentParser) -> None:
        p.add_argument("-m", "--module", help="run a module, the way python -m would")
        p.add_argument("script", nargs="?", help="path to a script")
        p.add_argument("args", nargs=argparse.REMAINDER, help="arguments for the program")
        p.add_argument("--policy", type=Path, help="policy file")
        p.add_argument("--show-reads", action="store_true", help="include reads in the report")
        p.add_argument("--json", action="store_true")
        p.add_argument("--limit", type=_rows, default=0, help="rows to show, 0 for all")

    plan = sub.add_parser("plan", help="run without touching anything, and report")
    target(plan)
    plan.add_argument(
        "--network",
        choices=["block", "allow"],
        default="block",
        help="block: refuse to reach the network, since a reply cannot be invented",
    )

    run = sub.add_parser("run", help="run for real, but only what the policy allows")
    target(run)

    check = sub.add_parser("check", help="plan, and exit non-zero if anything is destructive")
    target(check)
    check.add_argument(
        "--allow-destructive", action="store_true", help="only fail on policy denials"
    )
    check.add_argument("--network", choices=["block", "allow"], default="block")

    audit = sub.add_parser("audit", help="run for real and write down everything")
    target(audit)
    audit.add_argument("--log", type=Path, help="write a JSONL record here")

    log = sub.add_parser("log", help="read back a recorded run")
    log.add_argument("file", type=Path)
    log.add_argument("--kind", help="only this kind of effect")
    log.add_argument("--destructive", action="store_true", help="only destructive ones")
    log.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    from consequence.errors import Blocked, ConsequenceError
    from consequence.policy import PolicyError

    args = build_parser().parse_args(argv)
    try:
        if args.command == "log":
            return _log(args)
        return _execute(args)
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except (PolicyError, ConsequenceError, Blocked) as error:
        print(f"consequence: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(f"consequence: {error}", file=sys.stderr)
        return 2


def _session(args: argparse.Namespace) -> Any:
    import consequence
    from consequence.policy import load, permissive

    policy = load(args.policy) if args.policy else permissive()
    if args.command == "run":
        if args.policy is None:
            raise ValueError(
                "run needs a --policy. Running for real with nothing to say no is what "
                "'audit' is for."
            )
        return consequence.guard(policy)
    if args.command == "audit":
        return consequence.audit(policy=policy)
    return consequence.plan(policy=policy, network=getattr(args, "network", "block"))


def _run_target(args: argparse.Namespace) -> BaseException | None:
    """Execute the program, returning an exception rather than letting it escape.

    A program that fails partway is still worth reporting on. Half a plan plus
    the traceback beats no plan at all, which is what re-raising here would give.
    """
    argv = list(args.args)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if args.module and args.script:
        # argparse removes the first bare "--" itself, and the script positional
        # then swallows what was meant to be the program's first argument. So
        # `consequence plan -m pkg -- --url X up` reached pkg as `X up`, with the
        # flag silently gone. With -m there is no script, so whatever landed
        # there belongs to the program.
        argv = [args.script, *argv]
    try:
        if args.module:
            sys.argv = [args.module, *argv]
            runpy.run_module(args.module, run_name="__main__", alter_sys=True)
        else:
            script = str(Path(args.script).resolve())
            sys.argv = [script, *argv]
            sys.path.insert(0, str(Path(script).parent))
            runpy.run_path(script, run_name="__main__")
    except SystemExit as exit_error:
        if exit_error.code not in (0, None):
            return exit_error
    except KeyboardInterrupt:
        # BaseException below would otherwise swallow it, and the report would
        # claim the program failed rather than that somebody stopped it.
        raise
    except BaseException as error:
        return error
    return None


def _execute(args: argparse.Namespace) -> int:
    from consequence.report import render, style, summary
    from consequence.session import Mode

    if not args.module and not args.script:
        print("consequence: give it a script, or -m module", file=sys.stderr)
        return 2

    session = _session(args)
    original_argv = list(sys.argv)
    with session:
        failure = _run_target(args)
    sys.argv = original_argv

    if getattr(args, "log", None):
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("w", encoding="utf-8") as handle:
            for effect in session.effects:
                handle.write(json.dumps(effect.to_json()) + "\n")

    if args.json:
        print(
            json.dumps(
                {
                    "mode": session.mode.value,
                    "effects": session.to_json(),
                    "counts": session.counts(),
                    "denied": len(session.denied),
                    "destructive": len(session.destructive),
                    "failed": repr(failure) if failure else None,
                },
                indent=2,
            )
        )
    else:
        what = args.module or args.script
        print(
            render(
                session.effects,
                title=f"consequence {args.command}  ·  {what}",
                show_reads=args.show_reads,
                limit=args.limit,
            )
        )
        print()
        print(summary(session.effects, performed=session.mode is not Mode.PLAN))
        if getattr(args, "log", None):
            print(style(f"written to {args.log}", "dim"))
        if failure is not None:
            print()
            print(style(f"the program raised: {failure!r}", "yellow"))
            print(style("the report above covers everything up to that point", "dim"))

    if args.command == "check":
        blocking = list(session.denied)
        if not args.allow_destructive:
            blocking += [e for e in session.destructive if e.allowed]
        return 1 if blocking else 0
    if session.denied:
        return 1
    # A program that raised did not do what it was asked, and a shell script or
    # a CI step reading only the exit status would otherwise be told it did.
    # Its own SystemExit code is kept, since it chose that number for a reason.
    if isinstance(failure, SystemExit) and isinstance(failure.code, int):
        return failure.code
    if failure is not None:
        return 1
    return 0


def _log(args: argparse.Namespace) -> int:
    from consequence.report import table

    if not args.file.is_file():
        print(f"consequence: {args.file} does not exist", file=sys.stderr)
        return 2
    rows = []
    for line in args.file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue

    if args.kind:
        rows = [r for r in rows if r.get("kind") == args.kind]
    if args.destructive:
        rows = [r for r in rows if r.get("severity") in ("destroy", "external")]

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("nothing matched")
        return 0
    print(
        table(
            ["severity", "kind", "target", "where"],
            [
                (
                    r.get("severity", ""),
                    r.get("kind", ""),
                    r.get("target", "")[:48],
                    r.get("where", ""),
                )
                for r in rows
            ],
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
