# consequence

See what your code would do before it does it.

```bash
$ consequence plan cleanup_agent.py

consequence plan  ·  cleanup_agent.py

  > git              git push --force origin main    cleanup_agent.py:35 in main  EXTERNAL
  - config           delete directory                cleanup_agent.py:25 in main  DESTROY
  - keep-me.log      delete                          cleanup_agent.py:26 in main  DESTROY
  - app.db           drop table sessions             cleanup_agent.py:30 in main  DESTROY
  ~ config/app.toml  overwrite                       cleanup_agent.py:22 in main
  ~ app.db           delete from audit_log           cleanup_agent.py:31 in main

Plan: 2 to change, 3 to destroy, 1 external.
3 destructive effect(s). Nothing was performed.
```

The script really ran. It read its config, branched on it, opened the database.
None of it reached the disk, the network or the table.

## Install

```bash
pip install consequence
```

Python 3.10 and up. No dependencies beyond `tomli` on 3.10.

## Why

`terraform plan` works because Terraform owns every effect. Python owns none of
them, so the same question — *what is about to happen* — has no answer, and the
usual substitutes are a `--dry-run` flag somebody has to remember to implement
correctly, or reading the code and hoping.

That mattered more once code started arriving faster than anyone could read it.
An agent hands you forty lines that look reasonable. `shutil.rmtree` is in there
somewhere, on a path assembled three functions away. You can read it carefully,
or you can run it and find out.

## Three modes

**Plan.** Nothing reaches the outside world. Writes land in an overlay, so when
the program reads back the config it just wrote, it gets what it wrote. Programs
do this constantly — write, read, branch — and a dry run that swallows writes
sends the program down a path the real run would never take, then describes that
path to you as if it were the plan.

```python
import consequence

with consequence.plan() as run:
    deploy()

run.print()
```

**Guard.** Effects happen, but only the ones a policy allows. Everything else
raises `Denied` at the line that tried it, naming the rule that refused.

```python
with consequence.guard("safe.toml"):
    agent.run(task)
```

**Audit.** Everything happens, and everything is written down.

```bash
consequence audit --log run.jsonl deploy.py
consequence log run.jsonl --destructive
```

## What it sees

Files (read, write, append, delete, move, copy, chmod), directories, subprocesses
and signals, sockets and HTTP requests, environment variables, and SQLite.

SQLite goes through the authorizer callback rather than through string matching
on your SQL. The engine calls it while preparing each statement, with the action
code and table name already parsed, which means it cannot be fooled by unusual
SQL and it sees statements issued through any API, `executescript` and triggers
included. In plan mode your connection is an in-memory copy of the real database,
seeded with `backup()`, so `INSERT` then `SELECT` returns the row and the file on
disk never opens for writing.

Every effect carries the line that caused it, filtered down to code you wrote —
not the stdlib frame twelve levels down where the write actually happened.

## Policies

TOML, and small enough to read in one go.

```toml
default = "deny"

[filesystem]
read = ["**"]
write = ["./build/**", "./config/*.toml"]
delete = ["./build/**"]

[process]
allow = ["git status*", "git diff*"]

[network]
allow = ["api.github.com"]

[database]
allow = ["delete from audit_log", "insert into *"]
allow_schema_changes = false
```

Two rules, both load-bearing.

*Deny beats allow.* A path matching both is denied, so the meaning of a policy
never depends on the order the rules happen to be written in.

*There is no implicit default.* You write `allow` or `deny`. Guessing on the
reader's behalf is how a policy ends up meaning something nobody intended.

`*` does not cross a `/`; `**` does. Otherwise `/etc/*` matches
`/etc/nginx/nginx.conf` and the policy is far more permissive than it reads.

## In tests

```python
def test_rendering_is_pure(no_effects):
    render_invoice(rows)  # fails the test if it touches anything


def test_the_build_stays_put(effects, tmp_path):
    build(tmp_path)
    effects.assert_only_under(tmp_path)
```

Also `assert_no("process.spawn")` and `assert_nothing_destructive()`.

Tests that quietly write outside their `tmp_path` pass alone, pass in CI, and
then fail six months later because two of them raced on the same file in
somebody's home directory.

`no_effects` checks at teardown rather than blocking each write as it happens, so
a failure shows you everything the test wanted to do instead of stopping at the
first one. The cost is that pytest reports it as an error on a passing test. Call
`effects.assert_none()` yourself if you would rather it fail in the body.

## In CI

`check` plans and exits non-zero if anything would be destroyed.

```yaml
- run: pip install consequence
- run: consequence check --policy ci.toml scripts/migrate.py
```

## The limit

**This is not a sandbox.** It is in-process: the interceptors are patched into
`builtins`, `os`, `shutil`, `subprocess`, `socket` and `sqlite3` in the same
interpreter as your program. Code that goes around those — a C extension calling
`unlink(2)`, `ctypes`, a fork that re-execs — is not seen and not stopped.

So: run it on code you are unsure about, not on code you believe is hostile. For
hostile code you want a container, a VM, or seccomp, and those tools will not
tell you what the code was trying to do. That is the trade, and it is the reason
plan mode can hand a program its own writes back at all.

Two more things worth knowing. Plan mode refuses network calls rather than
inventing replies, because a fabricated response sends the program somewhere the
real one would not go; pass `network="allow"` if a read has to succeed for the
plan to get anywhere interesting. And plan mode copies a SQLite database into
memory, which it will not do above 512 MB.

## Examples

`examples/` has a runnable project and three scripts against it — plan, guard,
and the pytest fixtures. See [examples/README.md](examples/README.md).

## Related

`terraform plan` is the obvious ancestor. `strace` and `dtruss` see more, at the
syscall layer, after the fact, without policy or a plan. `pytest-socket` and
`pyfakefs` each cover one domain of this within tests. A container gives you a
real boundary and no idea what happened inside it.

## Licence

MIT.
