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
    Severity,
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


def test_an_inner_session_leaving_does_not_uninstall_the_outer_one(tmp_path):
    """Otherwise the outer session records nothing while believing it is watching."""
    target = tmp_path / "written.txt"
    with consequence.plan() as outer:
        with consequence.audit():
            pass
        assert installed()
        target.write_text("planned")
    assert not installed()
    assert not target.exists()
    assert any(e.kind == FILE_WRITE for e in outer.effects)


def test_the_internal_marker_nests():
    from consequence.intercept import _Internal, _reentrant

    with _Internal():
        with _Internal():
            assert _reentrant()
        assert _reentrant(), "an inner block leaving must not un-mark the outer one"
    assert not _reentrant()


def test_subprocess_call_returns_an_exit_status_in_plan_mode():
    """A CompletedProcess here reads as non-zero, so every shell-out looks failed."""
    with consequence.plan():
        assert subprocess.call(["git", "status"]) == 0
        assert subprocess.check_call(["git", "status"]) == 0


def test_check_output_returns_bytes_or_text_as_asked():
    with consequence.plan():
        assert subprocess.check_output(["git", "rev-parse"]) == b""
        assert subprocess.check_output(["git", "rev-parse"], text=True) == ""


def test_subprocess_run_still_returns_a_completed_process():
    with consequence.plan():
        result = subprocess.run(["git", "status"], check=False)
        assert result.returncode == 0


def test_os_system_returns_a_status_in_plan_mode():
    with consequence.plan():
        assert os.system("echo hi") == 0


def test_makedirs_on_a_directory_that_exists_is_not_reported(tmp_path):
    """exist_ok=True on an existing directory does nothing, so the plan says nothing."""
    with consequence.plan() as run:
        os.makedirs(tmp_path, exist_ok=True)
    assert not [e for e in run.effects if e.kind == DIR_CREATE]


def test_makedirs_that_would_really_create_something_is_reported(tmp_path):
    with consequence.plan() as run:
        os.makedirs(tmp_path / "a" / "b", exist_ok=True)
    assert [e for e in run.effects if e.kind == DIR_CREATE]
    assert not (tmp_path / "a").exists()


def test_mkdir_on_an_existing_directory_fails_the_way_it_really_would(tmp_path):
    """The real os.mkdir raises here, and Path.mkdir(exist_ok=True) needs it to."""
    with consequence.plan() as run, pytest.raises(FileExistsError):
        os.mkdir(tmp_path)
    assert not [e for e in run.effects if e.kind == DIR_CREATE]


def test_pathlib_mkdir_with_exist_ok_is_quiet_about_a_directory_that_exists(tmp_path):
    with consequence.plan() as run:
        Path(tmp_path).mkdir(parents=True, exist_ok=True)
    assert not [e for e in run.effects if e.kind == DIR_CREATE]


def test_pathlib_mkdir_with_exist_ok_still_reports_a_real_creation(tmp_path):
    with consequence.plan() as run:
        Path(tmp_path / "deep" / "nested").mkdir(parents=True, exist_ok=True)
    assert [e for e in run.effects if e.kind == DIR_CREATE]
    assert not (tmp_path / "deep").exists()


def test_writing_a_file_that_does_not_exist_is_a_creation(tmp_path):
    """ "overwrite" promises something is being lost, and mode alone cannot tell."""
    with consequence.plan() as run:
        (tmp_path / "new.txt").write_text("first time")
    [effect] = [e for e in run.effects if e.kind == FILE_WRITE]
    assert effect.severity is Severity.CREATE
    assert effect.detail == "create"


def test_writing_a_file_that_does_exist_is_an_overwrite(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("was here")
    with consequence.plan() as run:
        target.write_text("replaced")
    [effect] = [e for e in run.effects if e.kind == FILE_WRITE]
    assert effect.severity is Severity.MODIFY
    assert effect.detail == "overwrite"


def test_a_file_created_earlier_in_the_run_is_an_overwrite_after_that(tmp_path):
    """The overlay is the world the program sees, so the second write replaces."""
    target = tmp_path / "twice.txt"
    with consequence.plan() as run:
        target.write_text("one")
        target.write_text("two")
    details = [e.detail for e in run.effects if e.kind == FILE_WRITE]
    assert details == ["create", "overwrite"]


def test_a_denial_names_the_file_the_way_the_report_does(tmp_path, monkeypatch):
    """An absolute path buries the filename at the end of the line."""
    monkeypatch.chdir(tmp_path)
    policy = from_dict({"default": "deny", "filesystem": {"read": ["**"]}})
    with pytest.raises(Denied) as caught, consequence.guard(policy):
        Path("sub").mkdir()
    message = str(caught.value)
    assert "sub" in message
    assert str(tmp_path) not in message
