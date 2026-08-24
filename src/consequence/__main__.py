"""So ``python -m consequence`` works, not only the installed script.

Matters more here than for most tools: the thing being planned often runs in a
specific interpreter, and the plan has to run in that same one to see what it
imports.
"""

from consequence.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
