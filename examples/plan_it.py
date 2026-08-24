"""Plan mode from the library, rather than from the command line.

Nothing here reaches the disk, the network or the database. The program still
reads back its own writes, because plan mode hands them to it out of an overlay.
"""

import os

from setup_demo import build

import consequence


def main() -> int:
    os.chdir(build())

    with consequence.plan() as run:
        import cleanup_agent

        cleanup_agent.main()

    run.print(title="what cleanup_agent would do")

    if run.destructive:
        print(f"\n{len(run.destructive)} effect(s) you could not undo. Look before running it.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
