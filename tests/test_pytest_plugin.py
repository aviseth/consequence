"""The plugin, exercised the way a user meets it: as a real pytest run.

Run out of process. The fixtures install interceptors, and doing that inside the
interpreter that is already running the outer test suite means one test's patches
are live while another test is collecting.
"""

import pytest

pytestmark = pytest.mark.usefixtures("pytester")


def run(pytester, source, *args):
    pytester.makepyfile(source)
    return pytester.runpytest_subprocess("-p", "consequence", *args)


def test_a_pure_test_passes_under_no_effects(pytester):
    run(
        pytester,
        """
        def test_pure(no_effects):
            assert sum(range(10)) == 45
        """,
    ).assert_outcomes(passed=1)


def test_a_test_that_writes_is_reported_at_teardown(pytester):
    """The test body passes; the fixture is what fails, so this is an error.

    That is the cost of checking at teardown instead of blocking each write, and
    it buys the full list rather than only the first offender.
    """
    result = run(
        pytester,
        """
        import pathlib
        def test_writes(no_effects, tmp_path):
            pathlib.Path(tmp_path / "sneaky.txt").write_text("oops")
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*expected no side effects*", "*sneaky.txt*"])


def test_reading_is_not_a_side_effect(pytester):
    pytester.makefile(".txt", input="data")
    run(
        pytester,
        """
        import pathlib
        def test_reads(no_effects):
            assert pathlib.Path("input.txt").read_text().strip() == "data"
        """,
    ).assert_outcomes(passed=1)


def test_pytests_own_housekeeping_is_not_blamed_on_the_test(pytester):
    """tmp_path, PYTEST_CURRENT_TEST and __pycache__ all land inside the window."""
    run(
        pytester,
        """
        def test_uses_tmp_path(no_effects, tmp_path, monkeypatch):
            assert tmp_path.is_dir()
        """,
    ).assert_outcomes(passed=1)


def test_assert_only_under_passes_for_writes_inside_tmp_path(pytester):
    run(
        pytester,
        """
        def test_tidy(effects, tmp_path):
            (tmp_path / "out.txt").write_text("fine")
            effects.assert_only_under(tmp_path)
        """,
    ).assert_outcomes(passed=1)


def test_assert_only_under_catches_a_write_that_escapes(pytester):
    result = run(
        pytester,
        """
        def test_untidy(effects, tmp_path, monkeypatch):
            escapee = tmp_path.parent / "escaped.txt"
            escapee.write_text("outside")
            effects.assert_only_under(tmp_path)
        """,
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*expected writes only under*", "*escaped.txt*"])


def test_assert_no_names_the_kind_it_did_not_want(pytester):
    result = run(
        pytester,
        """
        import subprocess
        def test_no_subprocess(effects):
            subprocess.run(["true"], check=False)
            effects.assert_no("process.spawn")
        """,
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*expected no process.spawn*"])


def test_assert_nothing_destructive_lets_an_ordinary_write_through(pytester):
    run(
        pytester,
        """
        def test_writes_only(effects, tmp_path):
            (tmp_path / "a.txt").write_text("x")
            effects.assert_nothing_destructive()
        """,
    ).assert_outcomes(passed=1)


def test_assert_nothing_destructive_catches_a_delete(pytester):
    result = run(
        pytester,
        """
        def test_deletes(effects, tmp_path):
            target = tmp_path / "a.txt"
            target.write_text("x")
            target.unlink()
            effects.assert_nothing_destructive()
        """,
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*expected nothing destructive*"])


def test_the_recorder_hands_back_the_effects_themselves(pytester):
    run(
        pytester,
        """
        def test_inspect(effects, tmp_path):
            (tmp_path / "a.txt").write_text("x")
            kinds = {e.kind for e in effects.effects}
            assert "file.write" in kinds
            assert effects.writes()
        """,
    ).assert_outcomes(passed=1)


def test_the_failure_message_stops_listing_after_ten(pytester):
    result = run(
        pytester,
        """
        def test_many(no_effects, tmp_path):
            for i in range(15):
                (tmp_path / f"{i}.txt").write_text("x")
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*and 5 more*"])


def test_the_plugin_is_inert_until_a_fixture_asks_for_it(pytester):
    """Installing it must not change how an ordinary test behaves."""
    run(
        pytester,
        """
        import pathlib
        def test_ordinary(tmp_path):
            (tmp_path / "a.txt").write_text("x")
            assert (tmp_path / "a.txt").read_text() == "x"
        """,
    ).assert_outcomes(passed=1)


def test_a_helper_the_test_calls_still_counts(pytester):
    """The test's own frame is underneath the helper's, so it is the test's doing."""
    pytester.makepyfile(
        helper="""
        from pathlib import Path
        def save(path):
            Path(path).write_text("from a helper")
        """
    )
    result = run(
        pytester,
        """
        import helper
        def test_delegates(no_effects, tmp_path):
            helper.save(tmp_path / "out.txt")
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*out.txt*"])


def test_a_fixture_writing_on_its_own_behalf_is_not_the_tests_doing(pytester):
    pytester.makeconftest(
        """
        import pytest
        @pytest.fixture
        def scratch(tmp_path):
            (tmp_path / "fixture-owned.txt").write_text("mine")
            return tmp_path
        """
    )
    run(
        pytester,
        """
        def test_uses_it(no_effects, scratch):
            assert scratch.is_dir()
        """,
    ).assert_outcomes(passed=1)


def test_a_filesystem_root_allowlist_does_not_reject_everything(pytester):
    """Path("/") once built "//" and failed every path underneath it."""
    run(
        pytester,
        """
        import os
        def test_anywhere(effects, tmp_path):
            (tmp_path / "out.txt").write_text("x")
            effects.assert_only_under(os.path.sep)
        """,
    ).assert_outcomes(passed=1)
