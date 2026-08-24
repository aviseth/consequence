"""The pytest fixtures, as tests you can run: `pytest examples/test_purity.py`.

The failing ones are marked xfail so the file passes as a whole. Drop the marks
to see what the failure output looks like.
"""

import json
from pathlib import Path

import pytest


def render_invoice(rows):
    """Pure. Should not touch anything."""
    return json.dumps({"total": sum(rows)})


def render_invoice_badly(rows, cache=Path("/tmp/invoice-cache.json")):
    """Same function, with a cache somebody added on a Friday."""
    cache.write_text(json.dumps({"total": sum(rows)}))
    return cache.read_text()


def test_rendering_is_pure(no_effects):
    assert json.loads(render_invoice([1, 2, 3]))["total"] == 6


@pytest.mark.xfail(reason="demonstrates the failure output", strict=True)
def test_the_cache_is_caught(effects):
    """`no_effects` would catch this too, but at teardown, which pytest reports
    as an error on a passing test. Calling assert_none yourself fails in the
    body, which is what you want when the point is to show the message."""
    render_invoice_badly([1, 2, 3])
    effects.assert_none()


def test_a_build_stays_inside_its_directory(effects, tmp_path):
    (tmp_path / "out.txt").write_text("built")
    effects.assert_only_under(tmp_path)


def test_nothing_shells_out(effects):
    render_invoice([1, 2, 3])
    effects.assert_no("process.spawn")
