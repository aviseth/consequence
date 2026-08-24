"""See what your code would do before it does it.

    >>> import consequence
    >>> with consequence.plan() as run:
    ...     deploy()
    >>> run.print()

Three modes, one interception layer.

``plan``
    Nothing reaches the outside world. Writes land in an overlay so the program
    still reads back what it wrote, and you get a list of what would have
    happened.

``guard``
    Effects happen, but only the ones a policy allows. Anything else raises
    :class:`Denied` at the line that tried it.

``audit``
    Everything happens and everything is written down.
"""

from __future__ import annotations

from pathlib import Path

from consequence.effects import Effect, Frame, Severity
from consequence.errors import Blocked, ConsequenceError, Denied
from consequence.policy import Policy, permissive, read_only
from consequence.session import Mode, Network, Session, active

__all__ = [
    "Blocked",
    "ConsequenceError",
    "Denied",
    "Effect",
    "Frame",
    "Mode",
    "Network",
    "Policy",
    "Session",
    "Severity",
    "__version__",
    "active",
    "audit",
    "guard",
    "permissive",
    "plan",
    "read_only",
]

__version__ = "0.1.0"


def plan(
    *,
    policy: Policy | None = None,
    network: Network | str = Network.BLOCK,
) -> Session:
    """Run without touching anything, and collect what would have happened."""
    return Session(
        mode=Mode.PLAN,
        policy=policy or permissive(),
        network=Network(network),
    )


def guard(policy: Policy | str | Path, **kwargs: object) -> Session:
    """Let effects happen, but only the ones ``policy`` allows.

    A path is accepted for convenience, since a policy usually lives in a file
    next to the thing it governs.
    """
    from consequence.policy import load

    resolved = load(policy) if isinstance(policy, (str, Path)) else policy
    return Session(mode=Mode.GUARD, policy=resolved, **kwargs)  # type: ignore[arg-type]


def audit(*, policy: Policy | None = None) -> Session:
    """Let everything happen, and write down all of it."""
    return Session(mode=Mode.AUDIT, policy=policy or permissive())
