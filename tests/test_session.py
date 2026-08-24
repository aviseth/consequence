import pytest

import consequence
from consequence import effects as fx
from consequence.effects import Severity
from consequence.policy import Policy
from consequence.session import Mode, Network, Session, active


def test_no_session_is_active_outside_a_with_block():
    assert active() is None


def test_the_session_is_active_inside_and_gone_after():
    with consequence.audit() as session:
        assert active() is session
    assert active() is None


def test_plan_records_but_does_not_perform():
    session = Session(mode=Mode.PLAN)
    effect = session.check(fx.FILE_DELETE, "/tmp/x")
    assert effect.allowed
    assert not effect.performed


def test_audit_performs_what_it_records():
    session = Session(mode=Mode.AUDIT)
    assert session.check(fx.FILE_WRITE, "/tmp/x").performed


def test_a_denied_effect_is_never_performed():
    session = Session(mode=Mode.AUDIT, policy=Policy(default="deny"))
    effect = session.check(fx.FILE_WRITE, "/tmp/x")
    assert not effect.allowed
    assert not effect.performed


def test_enforce_raises_at_the_call_site_with_the_reason():
    session = Session(mode=Mode.GUARD, policy=Policy(default="deny"))
    effect = session.check(fx.FILE_DELETE, "/etc/passwd")
    with pytest.raises(consequence.Denied) as caught:
        session.enforce(effect)
    assert caught.value.effect is effect


def test_enforce_is_silent_when_the_effect_was_allowed():
    session = Session(mode=Mode.AUDIT)
    session.enforce(session.check(fx.FILE_WRITE, "/tmp/x"))


def test_an_effect_knows_where_it_came_from():
    session = Session(mode=Mode.PLAN)
    effect = session.check(fx.FILE_WRITE, "/tmp/x")
    assert effect.origin is not None
    assert effect.origin.file.endswith("test_session.py")
    assert effect.origin.function == "test_an_effect_knows_where_it_came_from"


def test_severity_defaults_to_the_kind_and_can_be_overridden():
    session = Session(mode=Mode.PLAN)
    assert session.check(fx.FILE_DELETE, "x").severity is Severity.DESTROY
    assert session.check(fx.FILE_WRITE, "x", severity=Severity.DESTROY).severity is Severity.DESTROY


def test_where_filters_by_kind_severity_and_denial():
    session = Session(mode=Mode.PLAN, policy=Policy(default="deny", filesystem={"write": ["**"]}))
    session.check(fx.FILE_WRITE, "/tmp/a")
    session.check(fx.FILE_DELETE, "/tmp/b")
    session.check(fx.FILE_READ, "/tmp/c")

    assert len(session.where(fx.FILE_WRITE)) == 1
    assert len(session.where([fx.FILE_WRITE, fx.FILE_DELETE])) == 2
    assert len(session.where(min_severity=Severity.DESTROY)) == 1
    assert [e.kind for e in session.where(denied=True)] == [fx.FILE_DELETE, fx.FILE_READ]
    assert [e.kind for e in session.where(denied=False)] == [fx.FILE_WRITE]


def test_destructive_and_denied_are_separate_questions():
    session = Session(mode=Mode.PLAN, policy=Policy(default="deny", filesystem={"delete": ["**"]}))
    session.check(fx.FILE_DELETE, "/tmp/gone")
    session.check(fx.FILE_WRITE, "/tmp/other")
    assert [e.kind for e in session.destructive] == [fx.FILE_DELETE]
    assert [e.kind for e in session.denied] == [fx.FILE_WRITE]


def test_reaching_another_system_counts_as_destructive():
    """Not because it destroys anything, but because undoing it is not ours to do."""
    session = Session(mode=Mode.PLAN)
    session.check(fx.NET_REQUEST, "example.com")
    assert session.destructive


def test_counts_are_grouped_by_kind():
    session = Session(mode=Mode.PLAN)
    session.check(fx.FILE_WRITE, "a")
    session.check(fx.FILE_WRITE, "b")
    session.check(fx.FILE_DELETE, "c")
    assert session.counts() == {fx.FILE_DELETE: 1, fx.FILE_WRITE: 2}


def test_json_carries_everything_needed_to_read_the_run_back():
    session = Session(mode=Mode.PLAN)
    session.check(fx.PROCESS_SPAWN, "git", "git push --force")
    [row] = session.to_json()
    assert row["kind"] == fx.PROCESS_SPAWN
    assert row["severity"] == "external"
    assert row["detail"] == "git push --force"
    assert "test_session.py" in row["where"]
    assert row["stack"]


def test_stdlib_frames_are_filtered_out_of_the_call_site():
    """The interesting line is the caller's, not json's or pathlib's.

    The value has to be one json cannot serialize, or ``default`` is never
    called and the test proves nothing about frames inside the stdlib.
    """
    import json

    session = Session(mode=Mode.PLAN)

    def default(_value):
        # Called from inside json.encoder, so every frame above this one is
        # stdlib until the test's own frame.
        session.check(fx.FILE_WRITE, "from-inside-json")
        return ""

    json.dumps({"k": object()}, default=default)

    [effect] = [e for e in session.effects if e.target == "from-inside-json"]
    assert effect.origin.file.endswith("test_session.py")
    assert not any("json" in f.file for f in effect.frames)


def test_a_frameless_run_still_records_something():
    """Filtering everything out would leave an effect nobody can locate."""
    session = Session(mode=Mode.PLAN, quiet=True)
    effect = session.check(fx.FILE_WRITE, "x")
    assert effect.frames


def test_plan_defaults_to_blocking_the_network():
    assert consequence.plan().network is Network.BLOCK
    assert consequence.plan(network="allow").network is Network.ALLOW


def test_guard_accepts_a_policy_file_path(tmp_path):
    path = tmp_path / "safe.toml"
    path.write_text('default = "deny"\n[filesystem]\nread = ["**"]\n')
    session = consequence.guard(str(path))
    assert session.mode is Mode.GUARD
    assert session.policy.default == "deny"


def test_the_helpers_pick_the_mode_they_say_they_do():
    assert consequence.plan().mode is Mode.PLAN
    assert consequence.audit().mode is Mode.AUDIT
    assert consequence.guard(Policy()).mode is Mode.GUARD


def test_nested_sessions_restore_the_outer_one():
    with consequence.audit() as outer, consequence.plan() as inner:
        assert active() is inner
    assert active() is None
    assert outer.mode is Mode.AUDIT


def test_an_effect_from_the_callers_own_code_is_not_internal():
    session = Session(mode=Mode.PLAN)
    assert not session.check(fx.FILE_WRITE, "x").internal


def test_an_effect_with_no_caller_frame_is_marked_internal(monkeypatch):
    """Framework housekeeping is recorded, but not blamed on whoever is watching.

    Every frame is declared noise, which is what the real case looks like: pytest
    creating a tmp_path, or the import system writing a __pycache__ entry, with
    nothing of the caller's anywhere in the stack.
    """
    import consequence.session as session_module

    monkeypatch.setattr(session_module, "_is_noise", lambda _filename: True)
    session = Session(mode=Mode.PLAN)
    effect = session.check(fx.FILE_WRITE, "x")
    assert effect.internal
    assert effect.frames, "an effect nobody can locate is useless"


def test_internal_survives_the_round_trip_to_json():
    session = Session(mode=Mode.PLAN)
    session.check(fx.FILE_WRITE, "x")
    assert session.to_json()[0]["internal"] is False


def test_print_writes_the_same_thing_report_returns(capsys):
    session = Session(mode=Mode.PLAN)
    session.check(fx.FILE_DELETE, "/tmp/gone")
    session.print(title="a plan")
    assert capsys.readouterr().out.strip() == session.report(title="a plan").strip()


def test_the_report_carries_both_the_lines_and_the_bottom_line():
    session = Session(mode=Mode.PLAN)
    session.check(fx.FILE_DELETE, "/tmp/gone")
    text = session.report()
    assert "/tmp/gone" in text
    assert "Plan: 1 to destroy" in text
