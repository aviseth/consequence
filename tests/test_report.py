from consequence import effects as fx
from consequence.effects import Effect, Frame, Severity
from consequence.report import MARKS, plain, render, style, summary, table


def effect(kind=fx.FILE_WRITE, target="/tmp/a", **kwargs):
    e = Effect(kind=kind, target=target, severity=fx.severity_of(kind), **kwargs)
    e.frames = [Frame(file="deploy.py", line=12, function="main")]
    return e


def test_each_severity_gets_its_own_mark():
    assert set(MARKS.values()) == {".", "+", "~", "-", ">"}


def test_the_worst_line_is_printed_first():
    out = plain(
        render([effect(fx.FILE_WRITE, "/tmp/a"), effect(fx.DIR_DELETE, "/tmp/d")], colour=False)
    )
    assert out.index("/tmp/d") < out.index("/tmp/a")


def test_reads_are_hidden_unless_asked_for():
    reads = [effect(fx.FILE_READ, "/etc/hosts")]
    assert "hosts" not in render(reads, colour=False)
    assert "hosts" in render(reads, show_reads=True, colour=False)


def test_nothing_to_show_says_so():
    assert "no effects" in render([], colour=False)


def test_the_call_site_is_on_the_line():
    assert "deploy.py:12 in main" in render([effect(fx.FILE_DELETE)], colour=False)


def test_a_denied_effect_is_marked_denied():
    denied = effect(fx.FILE_DELETE)
    denied.allowed = False
    out = render([denied], colour=False)
    assert "DENIED" in out
    assert "DESTROY" not in out


def test_destructive_effects_are_flagged():
    assert "DESTROY" in render([effect(fx.FILE_DELETE)], colour=False)


def test_a_limit_says_how_much_it_hid():
    out = render([effect(target=f"/tmp/{i}") for i in range(5)], limit=2, colour=False)
    assert "and 3 more" in out


def test_the_title_is_printed_when_given():
    assert "my plan" in render([], title="my plan", colour=False)


def test_a_plan_is_written_in_the_future_tense():
    line = summary([effect(fx.FILE_DELETE)], performed=False, colour=False)
    assert "1 to destroy" in line
    assert "Nothing was performed" in line


def test_a_finished_run_is_written_in_the_past_tense():
    line = summary([effect(fx.FILE_DELETE)], performed=True, colour=False)
    assert "1 destroyed" in line
    assert "Nothing was performed" not in line


def test_a_denied_effect_is_counted_as_neither():
    """A refused delete belongs in neither 'to destroy' nor 'destroyed'."""
    denied = effect(fx.FILE_DELETE)
    denied.allowed = False
    line = summary([denied], performed=True, colour=False)
    assert "destroyed" not in line
    assert "1 effect(s) denied by policy" in line


def test_an_empty_run_summarises_as_nothing():
    assert "nothing" in summary([], performed=False, colour=False)


def test_every_severity_appears_in_the_summary():
    line = summary(
        [
            effect(fx.DIR_CREATE, "/tmp/new"),
            effect(fx.FILE_WRITE, "/tmp/a"),
            effect(fx.FILE_DELETE, "/tmp/b"),
            effect(fx.NET_REQUEST, "example.com"),
        ],
        performed=False,
        colour=False,
    )
    for part in ("1 to create", "1 to change", "1 to destroy", "1 external"):
        assert part in line


def test_colour_can_be_turned_off_and_on():
    assert style("x", "red", enabled=False) == "x"
    assert style("x", "red", enabled=True) != "x"
    assert plain(style("x", "red", enabled=True)) == "x"


def test_a_table_lines_up_and_can_right_align():
    out = table(["n", "name"], [("1", "a"), ("22", "bb")], aligns="rl")
    rows = out.splitlines()
    assert rows[2].startswith(" 1")
    assert rows[3].startswith("22")


def test_an_empty_table_is_empty():
    assert table(["a"], []) == ""


def test_severity_ordering_is_the_reading_order():
    assert Severity.READ < Severity.CREATE < Severity.MODIFY < Severity.DESTROY
    assert Severity.DESTROY < Severity.EXTERNAL


def test_a_very_long_target_does_not_stretch_its_line():
    long = "/" + "d/" * 60 + "file.txt"
    out = plain(render([effect(fx.FILE_DELETE, long)], colour=False))
    assert len(out.splitlines()[0]) < 140
    assert "file.txt" in out, "the end identifies the file, so keep it"


def test_a_long_target_does_not_widen_the_short_ones():
    long = "/" + "d/" * 60 + "file.txt"
    out = plain(render([effect(fx.FILE_DELETE, long), effect(fx.FILE_DELETE, "/a")], colour=False))
    first, second = out.splitlines()[:2]
    assert abs(len(first) - len(second)) < 10
