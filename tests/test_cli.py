import json

import pytest

from consequence.cli import main

DESTRUCTIVE = """\
import os, pathlib
pathlib.Path("out.txt").write_text("written")
os.remove("doomed.txt")
"""

HARMLESS = """\
import pathlib
pathlib.Path("out.txt").write_text("written")
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "doomed.txt").write_text("still here")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write(project, source, name="script.py"):
    path = project / name
    path.write_text(source)
    return str(path)


def test_plan_reports_without_touching_anything(project, capsys):
    assert main(["plan", write(project, DESTRUCTIVE)]) == 0
    out = capsys.readouterr().out
    assert "out.txt" in out and "doomed.txt" in out
    assert "Nothing was performed" in out
    assert (project / "doomed.txt").exists()
    assert not (project / "out.txt").exists()


def test_check_fails_when_something_would_be_destroyed(project, capsys):
    assert main(["check", write(project, DESTRUCTIVE)]) == 1
    assert (project / "doomed.txt").exists()


def test_check_passes_when_nothing_is_destroyed(project):
    assert main(["check", write(project, HARMLESS)]) == 0


def test_check_can_be_told_to_only_care_about_policy(project):
    assert main(["check", "--allow-destructive", write(project, DESTRUCTIVE)]) == 0


def test_audit_actually_runs_the_program(project, capsys):
    assert main(["audit", write(project, DESTRUCTIVE)]) == 0
    assert (project / "out.txt").read_text() == "written"
    assert not (project / "doomed.txt").exists()
    assert "Performed:" in capsys.readouterr().out


def test_audit_writes_a_log_that_log_reads_back(project, capsys):
    main(["audit", "--log", "run.jsonl", write(project, HARMLESS)])
    capsys.readouterr()
    assert main(["log", "run.jsonl", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert any(r["kind"] == "file.write" for r in rows)


def test_log_can_filter_to_the_destructive_ones(project, capsys):
    main(["audit", "--log", "run.jsonl", write(project, DESTRUCTIVE)])
    capsys.readouterr()
    main(["log", "run.jsonl", "--destructive", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows and all(r["severity"] in ("destroy", "external") for r in rows)


def test_log_can_filter_by_kind(project, capsys):
    main(["audit", "--log", "run.jsonl", write(project, DESTRUCTIVE)])
    capsys.readouterr()
    main(["log", "run.jsonl", "--kind", "file.delete", "--json"])
    assert [r["kind"] for r in json.loads(capsys.readouterr().out)] == ["file.delete"]


def test_log_on_a_missing_file_is_an_error(project, capsys):
    assert main(["log", "nope.jsonl"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_run_refuses_without_a_policy(project, capsys):
    assert main(["run", write(project, HARMLESS)]) == 2
    assert "needs a --policy" in capsys.readouterr().err


def test_run_stops_the_program_at_a_denied_effect(project, capsys):
    (project / "safe.toml").write_text('default = "deny"\n[filesystem]\nread = ["**"]\n')
    assert main(["run", "--policy", "safe.toml", write(project, DESTRUCTIVE)]) == 1
    out = capsys.readouterr().out
    assert "DENIED" in out
    assert not (project / "out.txt").exists()


def test_run_lets_through_what_the_policy_allows(project):
    (project / "safe.toml").write_text(
        'default = "deny"\n[filesystem]\nread = ["**"]\nwrite = ["**"]\n'
    )
    assert main(["run", "--policy", "safe.toml", write(project, HARMLESS)]) == 0
    assert (project / "out.txt").read_text() == "written"


def test_a_program_that_raises_still_gets_a_report(project, capsys):
    source = 'import pathlib\npathlib.Path("out.txt").write_text("x")\nraise SystemExit(3)\n'
    main(["plan", write(project, source)])
    out = capsys.readouterr().out
    assert "out.txt" in out
    assert "the program raised" in out


def test_json_output_carries_the_counts(project, capsys):
    main(["plan", "--json", write(project, DESTRUCTIVE)])
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "plan"
    assert report["destructive"] >= 1
    assert report["counts"]["file.delete"] == 1


def test_arguments_after_a_dash_dash_reach_the_program(project, capsys):
    """The script itself checks argv, so a wrong value fails rather than passing quietly."""
    source = 'import sys\nassert sys.argv[1:] == ["hello"], sys.argv\n'
    main(["plan", "--json", write(project, source), "--", "hello"])
    report = json.loads(capsys.readouterr().out)
    assert report["failed"] is None


def test_a_module_can_be_run_the_way_python_m_would(project, capsys):
    (project / "mypkg").mkdir()
    (project / "mypkg" / "__init__.py").write_text("")
    (project / "mypkg" / "__main__.py").write_text(HARMLESS)
    import sys

    sys.path.insert(0, str(project))
    try:
        assert main(["plan", "-m", "mypkg"]) == 0
    finally:
        sys.path.remove(str(project))
        sys.modules.pop("mypkg", None)
    assert "out.txt" in capsys.readouterr().out


def test_nothing_to_run_is_an_error(project, capsys):
    assert main(["plan"]) == 2
    assert "give it a script" in capsys.readouterr().err


def test_a_broken_policy_file_is_reported_not_raised(project, capsys):
    (project / "bad.toml").write_text('default = "maybe"\n')
    assert main(["run", "--policy", "bad.toml", write(project, HARMLESS)]) == 2
    assert "consequence:" in capsys.readouterr().err


def test_reads_are_only_shown_when_asked_for(project, capsys):
    source = 'import pathlib\npathlib.Path("doomed.txt").read_text()\n'
    script = write(project, source)
    main(["plan", script])
    assert "doomed.txt" not in capsys.readouterr().out
    main(["plan", "--show-reads", script])
    assert "doomed.txt" in capsys.readouterr().out


def test_the_argv_the_program_saw_is_put_back(project):
    import sys

    before = list(sys.argv)
    main(["plan", write(project, HARMLESS)])
    assert sys.argv == before


def test_a_failed_target_is_a_failed_command(project, capsys):
    source = "raise SystemExit(3)\n"
    assert main(["plan", write(project, source)]) == 3


def test_an_exception_in_the_target_is_a_failed_command(project, capsys):
    source = "raise ValueError('nope')\n"
    assert main(["audit", write(project, source)]) == 1


def test_a_target_that_exits_cleanly_is_not_a_failure(project):
    assert main(["plan", write(project, "raise SystemExit(0)\n")]) == 0


def test_a_negative_limit_is_rejected(project, capsys):
    with pytest.raises(SystemExit):
        main(["plan", "--limit", "-1", write(project, HARMLESS)])
    assert "cannot be negative" in capsys.readouterr().err


def test_a_policy_file_can_be_a_path_object(tmp_path):
    import consequence

    path = tmp_path / "safe.toml"
    path.write_text('default = "deny"\n[filesystem]\nread = ["**"]\n')
    assert consequence.guard(path).policy.default == "deny"


def test_flags_survive_between_dash_dash_and_a_module(project, capsys):
    """argparse eats the -- and the script positional then eats the first flag."""
    (project / "mypkg").mkdir()
    (project / "mypkg" / "__init__.py").write_text("")
    (project / "mypkg" / "__main__.py").write_text(
        'import sys\nassert sys.argv[1:] == ["--url", "sqlite:///x.db", "up"], sys.argv\n'
    )
    import sys

    sys.path.insert(0, str(project))
    try:
        main(["plan", "--json", "-m", "mypkg", "--", "--url", "sqlite:///x.db", "up"])
    finally:
        sys.path.remove(str(project))
        sys.modules.pop("mypkg", None)
        sys.modules.pop("mypkg.__main__", None)
    assert json.loads(capsys.readouterr().out)["failed"] is None
