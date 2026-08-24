from pathlib import Path

from consequence.overlay import Overlay


def test_a_write_is_readable_without_touching_disk(tmp_path):
    overlay, target = Overlay(), tmp_path / "config.toml"
    overlay.write(target, b"debug = true")
    assert overlay.read(target) == b"debug = true"
    assert not target.exists()


def test_a_read_falls_through_to_the_real_file(tmp_path):
    real = tmp_path / "real.txt"
    real.write_bytes(b"on disk")
    assert Overlay().read(real) == b"on disk"


def test_a_write_shadows_the_real_file(tmp_path):
    real = tmp_path / "real.txt"
    real.write_bytes(b"on disk")
    overlay = Overlay()
    overlay.write(real, b"overlaid")
    assert overlay.read(real) == b"overlaid"
    assert real.read_bytes() == b"on disk"


def test_a_delete_hides_a_file_that_is_still_there(tmp_path):
    real = tmp_path / "real.txt"
    real.write_bytes(b"on disk")
    overlay = Overlay()
    overlay.delete(real)
    assert overlay.read(real) is None
    assert not overlay.exists(real)
    assert real.exists()


def test_appending_starts_from_the_real_contents(tmp_path):
    real = tmp_path / "log"
    real.write_bytes(b"first\n")
    overlay = Overlay()
    overlay.append(real, b"second\n")
    assert overlay.read(real) == b"first\nsecond\n"


def test_appending_to_a_deleted_file_starts_empty(tmp_path):
    real = tmp_path / "log"
    real.write_bytes(b"first\n")
    overlay = Overlay()
    overlay.delete(real)
    overlay.append(real, b"second\n")
    assert overlay.read(real) == b"second\n"


def test_writing_after_deleting_brings_the_file_back(tmp_path):
    target = tmp_path / "f"
    overlay = Overlay()
    overlay.delete(target)
    overlay.write(target, b"again")
    assert overlay.exists(target)
    assert overlay.read(target) == b"again"


def test_deleting_a_tree_hides_everything_under_it(tmp_path):
    overlay = Overlay()
    overlay.write(tmp_path / "d" / "a.txt", b"a")
    overlay.write(tmp_path / "d" / "sub" / "b.txt", b"b")
    overlay.delete_tree(tmp_path / "d")
    assert not overlay.exists(tmp_path / "d" / "a.txt")
    assert not overlay.exists(tmp_path / "d" / "sub" / "b.txt")


def test_a_real_file_under_a_deleted_tree_is_hidden_too(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "real.txt").write_bytes(b"x")
    overlay = Overlay()
    overlay.delete_tree(tmp_path / "d")
    assert not overlay.exists(tmp_path / "d" / "real.txt")


def test_mkdir_makes_a_directory_that_is_not_on_disk(tmp_path):
    overlay, made = Overlay(), tmp_path / "new"
    overlay.mkdir(made)
    assert overlay.exists(made)
    assert not made.exists()


def test_moving_carries_the_contents_and_leaves_nothing_behind(tmp_path):
    overlay = Overlay()
    overlay.write(tmp_path / "a", b"payload")
    overlay.move(tmp_path / "a", tmp_path / "b")
    assert overlay.read(tmp_path / "b") == b"payload"
    assert not overlay.exists(tmp_path / "a")


def test_moving_a_real_file_reads_it_off_disk_first(tmp_path):
    (tmp_path / "a").write_bytes(b"from disk")
    overlay = Overlay()
    overlay.move(tmp_path / "a", tmp_path / "b")
    assert overlay.read(tmp_path / "b") == b"from disk"
    assert (tmp_path / "a").exists()


def test_two_spellings_of_one_path_are_one_entry(tmp_path):
    """Otherwise a program that writes ./x and reads x sees two different files."""
    overlay = Overlay()
    overlay.write(tmp_path / "x", b"once")
    assert overlay.read(Path(str(tmp_path) + "/./x")) == b"once"


def test_touched_lists_everything_that_changed(tmp_path):
    overlay = Overlay()
    overlay.write(tmp_path / "a", b"")
    overlay.delete(tmp_path / "b")
    overlay.mkdir(tmp_path / "c")
    assert len(overlay.touched) == 4  # a, b, c, and a's parent directory


def test_an_untouched_overlay_summarises_as_nothing():
    assert Overlay().summary() == "nothing"
