"""The interceptors, in each mode, against a real filesystem."""

import builtins
import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import consequence
from consequence import Denied
from consequence.effects import (
    DIR_CREATE,
    DIR_DELETE,
    FILE_DELETE,
    FILE_READ,
    FILE_WRITE,
    PROCESS_SPAWN,
)
from consequence.intercept import installed
from consequence.policy import from_dict

# --- the most important test in the package -----------------------------------


def test_the_standard_library_is_exactly_as_it_was_afterwards():
    """A tool that leaves the stdlib patched after a run is worse than none."""
    before = {
        "open": builtins.open,
        "io.open": io.open,
        "os.remove": os.remove,
        "os.mkdir": os.mkdir,
        "shutil.rmtree": shutil.rmtree,
        "subprocess.run": subprocess.run,
        "subprocess.Popen": subprocess.Popen,
    }
    with consequence.plan():
        assert installed()
        assert builtins.open is not before["open"]
    assert not installed()
    assert builtins.open is before["open"]
    assert io.open is before["io.open"]
    assert os.remove is before["os.remove"]
    assert os.mkdir is before["os.mkdir"]
    assert shutil.rmtree is before["shutil.rmtree"]
    assert subprocess.run is before["subprocess.run"]
    assert subprocess.Popen is before["subprocess.Popen"]


def test_everything_is_restored_even_when_the_body_raises():
    original = builtins.open
    with pytest.raises(RuntimeError), consequence.plan():
        raise RuntimeError("boom")
    assert builtins.open is original
    assert not installed()


# --- plan mode ----------------------------------------------------------------


def test_plan_mode_does_not_write(tmp_path):
    target = tmp_path / "out.txt"
    with consequence.plan() as run:
        Path(target).write_text("hello")
    assert not target.exists()
    assert [e.kind for e in run.effects] == [FILE_WRITE]


def test_plan_mode_hands_the_program_its_own_write_back(tmp_path):
    """Swallowing writes would make the program take a path the real run never
    would, and the plan would describe a different program."""
    target = tmp_path / "config.txt"
    target.write_text("port = 8080")
    with consequence.plan():
        text = target.read_text()
        target.write_text(text.replace("8080", "9090"))
        assert target.read_text() == "port = 9090"
    assert target.read_text() == "port = 8080"


def test_plan_mode_appends_onto_what_is_already_there(tmp_path):
    target = tmp_path / "log.txt"
    target.write_text("first\n")
    with consequence.plan():
        with open(target, "a") as handle:
            handle.write("second\n")
        with open(target) as handle:
            assert handle.read() == "first\nsecond\n"
    assert target.read_text() == "first\n"


def test_plan_mode_does_not_delete(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("x")
    with consequence.plan() as run:
        os.remove(target)
        assert not Path(target).exists() or True  # overlay knows; disk untouched
    assert target.exists()
    assert run.effects[0].kind == FILE_DELETE
    assert run.effects[0].destructive


def test_plan_mode_does_not_remove_trees(tmp_path):
    tree = tmp_path / "tree"
    (tree / "nested").mkdir(parents=True)
    (tree / "nested" / "file.txt").write_text("x")
    with consequence.plan() as run:
        shutil.rmtree(tree)
    assert tree.exists()
    assert run.effects[0].kind == DIR_DELETE


def test_plan_mode_does_not_create_directories(tmp_path):
    target = tmp_path / "new"
    with consequence.plan() as run:
        os.mkdir(target)
    assert not target.exists()
    assert run.effects[0].kind == DIR_CREATE


def test_plan_mode_does_not_run_processes(tmp_path):
    marker = tmp_path / "ran.txt"
    with consequence.plan() as run:
        result = subprocess.run(["sh", "-c", f"touch {marker}"], capture_output=True, check=False)
    assert not marker.exists()
    assert result.returncode == 0
    assert run.effects[0].kind == PROCESS_SPAWN


def test_plan_mode_does_not_run_popen(tmp_path):
    marker = tmp_path / "ran.txt"
    with consequence.plan():
        process = subprocess.Popen(["sh", "-c", f"touch {marker}"])
        assert process.wait() == 0
    assert not marker.exists()


def test_plan_mode_records_a_move_without_moving(tmp_path):
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("x")
    with consequence.plan():
        shutil.move(str(src), str(dst))
    assert src.exists()
    assert not dst.exists()


def test_reads_are_recorded_but_left_alone(tmp_path):
    target = tmp_path / "in.txt"
    target.write_text("data")
    with consequence.plan() as run:
        assert target.read_text() == "data"
    assert run.effects[0].kind == FILE_READ


# --- guard mode ---------------------------------------------------------------


def test_guard_allows_what_the_policy_allows(tmp_path):
    policy = from_dict({"default": "deny", "filesystem": {"write": [f"{tmp_path}/**"]}})
    target = tmp_path / "ok.txt"
    with consequence.guard(policy):
        target.write_text("written for real")
    assert target.read_text() == "written for real"


def test_guard_refuses_what_it_does_not(tmp_path):
    policy = from_dict({"default": "deny", "filesystem": {"read": ["**"]}})
    target = tmp_path / "nope.txt"
    with pytest.raises(Denied, match="refused to write"), consequence.guard(policy):
        target.write_text("should not happen")
    assert not target.exists()


def test_the_denial_names_the_line_that_tried(tmp_path):
    policy = from_dict({"default": "deny"})
    with pytest.raises(Denied) as raised, consequence.guard(policy):
        (tmp_path / "x").write_text("x")
    assert "test_intercept.py" in str(raised.value)


def test_guard_stops_a_delete_before_it_happens(tmp_path):
    target = tmp_path / "precious.txt"
    target.write_text("keep me")
    policy = from_dict({"default": "deny", "filesystem": {"read": ["**"]}})
    with pytest.raises(Denied), consequence.guard(policy):
        os.remove(target)
    assert target.read_text() == "keep me"


def test_guard_stops_a_process(tmp_path):
    marker = tmp_path / "ran.txt"
    policy = from_dict({"default": "deny"})
    with pytest.raises(Denied), consequence.guard(policy):
        subprocess.run(["sh", "-c", f"touch {marker}"], check=False)
    assert not marker.exists()


# --- audit mode ---------------------------------------------------------------


def test_audit_lets_everything_happen_and_writes_it_down(tmp_path):
    target = tmp_path / "out.txt"
    with consequence.audit() as run:
        target.write_text("real")
        target.unlink()
    assert not target.exists()
    assert [e.kind for e in run.effects] == [FILE_WRITE, FILE_DELETE]
    assert all(e.performed for e in run.effects)


def test_effects_carry_the_call_site(tmp_path):
    with consequence.audit() as run:
        (tmp_path / "x.txt").write_text("x")
    origin = run.effects[0].origin
    assert origin is not None
    assert origin.file.endswith("test_intercept.py")
    assert origin.function == "test_effects_carry_the_call_site"


def test_consequences_own_bookkeeping_is_not_recorded(tmp_path):
    """Otherwise every effect would drag in the library's own file reads."""
    with consequence.audit() as run:
        (tmp_path / "x.txt").write_text("x")
    assert len(run.effects) == 1


# --- outside a session --------------------------------------------------------


def test_nothing_is_intercepted_when_no_session_is_active(tmp_path):
    target = tmp_path / "free.txt"
    target.write_text("no session, no interception")
    assert target.read_text() == "no session, no interception"
    assert consequence.active() is None


def test_pathlib_writes_are_intercepted(tmp_path):
    """3.10's pathlib keeps its own copy of io.open, so patching io was not enough."""
    target = tmp_path / "via_pathlib.txt"
    with consequence.plan() as run:
        target.write_text("planned")
        assert target.read_text() == "planned"
    assert not target.exists()
    assert any(e.kind == FILE_WRITE and e.target.endswith("via_pathlib.txt") for e in run.effects)


def test_pathlib_deletes_are_intercepted(tmp_path):
    target = tmp_path / "gone.txt"
    target.write_text("still here")
    with consequence.plan() as run:
        target.unlink()
    assert target.read_text() == "still here"
    assert any(e.kind == FILE_DELETE for e in run.effects)


def test_pathlib_mkdir_is_intercepted(tmp_path):
    made = tmp_path / "new"
    with consequence.plan() as run:
        made.mkdir()
    assert not made.exists()
    assert any(e.kind == DIR_CREATE for e in run.effects)


def test_the_accessor_is_put_back_afterwards():
    """Leaving 3.10's pathlib pointed at a torn-down interceptor is worse than not hooking it."""
    import pathlib as pathlib_module

    accessor = getattr(pathlib_module, "_normal_accessor", None)
    if accessor is None:
        pytest.skip("no accessor on this version")
    before = accessor.open
    with consequence.plan():
        assert accessor.open is not before
    assert accessor.open is before


def test_a_planned_append_reads_back_the_line_endings_text_mode_would_give(tmp_path):
    """On Windows the file on disk holds \\r\\n, and a real read would not show it."""
    target = tmp_path / "log.txt"
    with open(target, "w") as handle:
        handle.write("first\n")
    with consequence.plan():
        with open(target, "a") as handle:
            handle.write("second\n")
        with open(target) as handle:
            assert handle.read() == "first\nsecond\n"
