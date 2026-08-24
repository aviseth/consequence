# Changelog

## 0.1.0

First release.

- Plan, guard and audit modes over one interception layer.
- Filesystem, process, network, environment and SQLite effects.
- Copy-on-write overlay so plan mode reads back the program's own writes.
- SQLite intercepted through the authorizer callback, with plan mode running
  against an in-memory copy of the real database.
- TOML policies with `**` globs, deny-wins ordering and a required default.
- `consequence plan | run | check | audit | log`.
- `effects` and `no_effects` pytest fixtures.
