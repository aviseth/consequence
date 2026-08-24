# Contributing

Thanks for looking. This is a small project and the bar is honest work, not
perfect work — a rough patch with a test beats a polished one without.

## Getting set up

```bash
git clone https://github.com/aviseth/consequence
cd consequence
uv sync
uv run pytest
```

Everything CI checks, in one line:

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

CI runs the suite on Python 3.10 through 3.14 on Linux, and on 3.12 and 3.14 on
macOS and Windows. Both platform legs earn their keep: 3.10 caught `pathlib`
holding its own copy of `io.open`, and Windows caught policy globs matching
nothing at all.

Run `ruff format` **before** writing documentation, not after. It formats Python
inside Markdown fences, which has broken CI on three of these repos.

## What would help most

**A fidelity bug, with a failing test.** An effect that happened for real but was
missing from the plan, or reported but never happens. These are the bugs that
matter here — everything else is a tool that is merely inconvenient, and this one
is a tool people trust before doing something irreversible.

`examples/fidelity_check.py` runs any program twice, planned and for real, and
diffs the two. It is the fastest way to find one.

**A new interceptor.** `mmap`, `os.sendfile` and future `pathlib` internals are
not covered. The pattern is in `src/consequence/intercept.py`; each is roughly
fifteen lines and two tests. Intercept at the lowest layer that still knows what
is going on — patching `os.unlink` catches `Path.unlink` and `shutil.rmtree` for
free, and patching both would count the same deletion twice.

**Another database.** SQLite is done properly, through the authorizer callback.
Postgres and MySQL need a different approach, probably at the driver's `execute`,
and the design is worth an issue before the code.

**Policy expressiveness, carefully.** Every feature here is a new way for a
policy to mean something its author did not intend. Bring a case where the
current rules genuinely cannot express something safe.

## Conventions

**Tests are sentences.** `test_a_drop_is_reported_once_not_as_a_drop_plus_a_delete`,
not `test_drop_dedup`. The name should say what the behaviour is, so a failure
reads as a claim that stopped being true.

**Comments explain why, not what.** Especially where the obvious approach was
tried first and did not work. Several comments in `intercept.py` and
`databases.py` are tombstones for real bugs — the one about `sqlite3.Connection`
being a C type, the one about `SQLITE_IGNORE` not stopping a top-level `DELETE`
— and they are there so nobody re-introduces them. If you remove one, say why in
the pull request.

**Prefer a test that would have caught the bug** over one that describes the fix.
The `SQLITE_IGNORE` bug was found by a test that counted rows afterwards; one
that trusted the return value would have passed.

**A safety tool fails loudly.** If an interceptor cannot be installed, raise. Do
not `suppress`. An earlier version did, and reported a clean plan for a program
that dropped a table.

## Pull requests

Open an issue first for anything structural. For a fix, a PR is fine directly.

- One change per PR where you can manage it.
- Include a test that fails before and passes after.
- Update `CHANGELOG.md` under an `Unreleased` heading.
- CI must be green. If a platform leg fails and you cannot reproduce it, say so
  in the PR rather than disabling it — that leg has found two real bugs so far.

Pull requests get an automated review from CodeRabbit as well as a human one.
Disagreeing with it is fine; say why in a reply rather than silently ignoring it.

## Releases

Maintainer only. Tag `vX.Y.Z` matching the version in `pyproject.toml`; the
release workflow re-runs the whole suite, builds, verifies the wheel installs and
runs on its own, and publishes to PyPI behind an environment approval.

## Code of conduct

Be decent. Assume the other person is trying to help. That is the whole thing.
