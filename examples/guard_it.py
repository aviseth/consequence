"""Guard mode: the program runs for real, but only inside a policy.

build() sets up examples/demo first, outside the session and for real.

The first thing safe.toml does not allow raises Denied at the line that tried
it, with the rule that refused it. Everything before that point really happened,
which is the honest shape of a policy failure and not something to hide.
"""

import os

from setup_demo import build

import consequence


def main() -> int:
    os.chdir(build())

    with consequence.guard("safe.toml") as run:
        try:
            import cleanup_agent

            cleanup_agent.main()
        except consequence.Denied as denied:
            print(f"stopped: {denied}\n")

    run.print(title="what actually happened before it stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
