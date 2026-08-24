# Changelog

## 0.1.0

First release.

- Plan, guard and audit modes over one interception layer.
- Filesystem, directory, process, network, environment and SQLite effects, each
  carrying the line in your own code that caused it.
- Copy-on-write overlay, so plan mode hands a program back its own writes and it
  follows the branch the real run would.
- SQLite intercepted through the authorizer callback rather than by matching on
  SQL text; plan mode runs against an in-memory copy of the real database.
- TOML policies: `**` globs, deny beats allow, and a required default.
- `consequence plan | run | check | audit | log`.
- `effects` and `no_effects` pytest fixtures.
- `examples/fidelity_check.py`, which runs any program planned and for real and
  reports whether the plan was true.
