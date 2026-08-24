"""A pytest fixture for asserting what a test is allowed to touch.

    def test_render_is_pure(no_effects):
        render_report(data)          # fails the test if it writes anything

    def test_writes_only_to_tmp(effects):
        build(tmp_path)
        effects.assert_only_under(tmp_path)

Tests that quietly write outside their tmp_path are a real and annoying class of
bug: they pass alone, pass in CI, and then one day fail because two of them
raced on the same file in somebody's home directory.

pytest imports every installed plugin at startup, so nothing here is imported
until a fixture is actually used.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


class EffectRecorder:
    """The effects a test produced, with assertions worth writing."""

    def __init__(self, session: Any, test_file: str = "") -> None:
        self.session = session
        self.test_file = test_file

    @property
    def effects(self) -> list[Any]:
        """What the test itself did.

        pytest is busy during a test: it creates tmp_path, sets
        PYTEST_CURRENT_TEST, and the import system writes __pycache__ entries.
        All of that lands inside the recording window and none of it is the
        test's doing, so an effect counts only if the test file appears
        somewhere in its call stack. Helpers the test calls are still counted,
        because the test's own frame is underneath them; a fixture writing on
        its own behalf is not, because it is not.

        The narrower check of "did any frame survive noise filtering" was tried
        first and is not enough. pytest's console script lives in the venv's bin
        directory, which is neither stdlib nor site-packages, so it survives, and
        every assertion fails on a __pycache__ entry it did not create.
        """
        if not self.test_file:  # pragma: no cover - fixtures always set it
            return [e for e in self.session.effects if not e.internal]
        return [e for e in self.session.effects if any(f.file == self.test_file for f in e.frames)]

    @property
    def all_effects(self) -> list[Any]:
        """Everything recorded, framework housekeeping included."""
        return list(self.session.effects)

    def writes(self) -> list[Any]:
        from consequence.effects import Severity

        return [e for e in self.effects if e.severity >= Severity.CREATE]

    def assert_none(self) -> None:
        """No effect beyond reading."""
        offenders = self.writes()
        if offenders:
            pytest.fail(_report("expected no side effects, but the test", offenders))

    def assert_only_under(self, *roots: str | os.PathLike[str]) -> None:
        """Every write lands somewhere under one of ``roots``."""
        # rstrip the separator before appending one: a root of "/" would
        # otherwise build "//" and reject every path underneath it.
        allowed = [str(Path(r).resolve()).rstrip(os.sep) or os.sep for r in roots]
        offenders = [
            e
            for e in self.writes()
            if not any(
                str(Path(e.target).resolve()) == root
                or str(Path(e.target).resolve()).startswith(root.rstrip(os.sep) + os.sep)
                for root in allowed
            )
        ]
        if offenders:
            where = ", ".join(allowed)
            pytest.fail(_report(f"expected writes only under {where}, but the test", offenders))

    def assert_no(self, kind: str) -> None:
        offenders = [e for e in self.effects if e.kind == kind]
        if offenders:
            pytest.fail(_report(f"expected no {kind}, but the test", offenders))

    def assert_nothing_destructive(self) -> None:
        offenders = [e for e in self.effects if e.destructive]
        if offenders:
            pytest.fail(_report("expected nothing destructive, but the test", offenders))


def _report(prefix: str, offenders: list[Any]) -> str:
    lines = [f"{prefix}:"]
    for effect in offenders[:10]:
        where = f"  ({effect.origin})" if effect.origin else ""
        lines.append(f"  {effect.describe()}{where}")
    if len(offenders) > 10:
        lines.append(f"  ... and {len(offenders) - 10} more")
    return "\n".join(lines)


@pytest.fixture
def effects(request: pytest.FixtureRequest) -> Iterator[EffectRecorder]:
    """Watch what the test does, and let it assert on that afterwards."""
    import consequence

    with consequence.audit() as session:
        yield EffectRecorder(session, str(request.path))


@pytest.fixture
def no_effects(request: pytest.FixtureRequest) -> Iterator[EffectRecorder]:
    """Watch, and fail the test at the end if it changed anything.

    Deliberately checked at teardown rather than blocking each effect as it
    happens. A test that would write should show you everything it wanted to do,
    not stop at the first one and make you run it again to find the second.
    """
    import consequence

    with consequence.audit() as session:
        recorder = EffectRecorder(session, str(request.path))
        yield recorder
    recorder.assert_none()
